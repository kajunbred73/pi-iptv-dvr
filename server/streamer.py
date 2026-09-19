"""ffmpeg-based live HLS proxy and DVR recorder."""
import logging
import os
import re
import shutil
import subprocess
import threading
import time

import config
import db

log = logging.getLogger("streamer")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


_XTREAM_TS = re.compile(r"(/live/[^/]+/[^/]+/\d+)\.ts$")


def _input_url(url):
    if config.get("xtream_hls_input"):
        return _XTREAM_TS.sub(r"\1.m3u8", url)
    return url


def _input_args(url):
    url = _input_url(url)
    # IPTV feeds are full of corrupt packets and timestamp jumps; drop the junk and let ffmpeg
    # regenerate timestamps so the copied-through output stays monotonic (otherwise the Roku
    # jumps back every few seconds).
    args = ["-hide_banner", "-loglevel", "warning", "-nostdin",
            "-fflags", "+genpts+discardcorrupt+igndts", "-err_detect", "ignore_err",
            "-analyzeduration", "3000000", "-probesize", "5000000"]
    if url.startswith("http"):
        # Xtream .ts streams are closed by the provider mid-transfer at random offsets;
        # on_network_error + at_eof keep ffmpeg reconnecting instead of dying.
        args += ["-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
                 "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5",
                 # Stamp packets with arrival time instead of trusting the source clock: some
                 # feeds jump their timestamps (multi-day discontinuities on reconnect), which
                 # corrupts the HLS muxer's segment table and yields playlists with no EXTINF.
                 "-use_wallclock_as_timestamps", "1",
                 "-user_agent", config.get("user_agent")]
    else:
        args += ["-re"]  # local files: read in real time
    args += ["-i", url]
    return args


def _copy_args():
    # Remux only (no transcoding) so a Pi can keep up. Roku plays H.264/AAC HLS,
    # which is what nearly all IPTV sources already are.
    # Some feeds send SPS/PPS only once at stream start, so every HLS segment after the first
    # is undecodable on its own and the Roku dies as soon as it crosses a segment boundary;
    # dump_extra re-inserts them before every keyframe.
    return ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-bsf:v", "dump_extra=freq=keyframe",
            "-sn", "-dn",
            "-avoid_negative_ts", "make_zero", "-max_interleave_delta", "0",
            "-muxdelay", "0", "-muxpreload", "0"]


# ---------------------------------------------------------------- live proxy

class LiveSession:
    def __init__(self, channel):
        self.channel = channel
        self.dir = os.path.join(config.get("live_dir"), str(channel["id"]))
        self.playlist = os.path.join(self.dir, "index.m3u8")
        self.proc = None
        self.last_access = time.time()

    def start(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, exist_ok=True)
        cmd = [FFMPEG] + _input_args(self.channel["url"]) + _copy_args() + [
            "-f", "hls",
            "-hls_time", str(config.get("hls_segment_seconds")),
            "-hls_list_size", str(config.get("hls_list_size")),
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-hls_segment_filename", os.path.join(self.dir, "seg%05d.ts"),
            self.playlist,
        ]
        log.info("live start ch=%s", self.channel["id"])
        self.proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            log.debug("ffmpeg[%s]: %s", self.channel["id"], line.decode(errors="replace").rstrip())

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def wait_ready(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(self.playlist) and ".ts" in open(self.playlist).read():
                return True
            if not self.alive():
                return False
            time.sleep(0.25)
        return False

    def stop(self):
        log.info("live stop ch=%s", self.channel["id"])
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


class LiveManager:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._reaper, daemon=True).start()

    def get(self, channel):
        cid = channel["id"]
        with self.lock:
            s = self.sessions.get(cid)
            if s is None or not s.alive():
                s = LiveSession(channel)
                s.start()
                self.sessions[cid] = s
            s.last_access = time.time()
        return s

    def touch(self, cid):
        s = self.sessions.get(cid)
        if s:
            s.last_access = time.time()
        return s

    def _reaper(self):
        while True:
            time.sleep(5)
            idle = config.get("live_idle_seconds")
            with self.lock:
                for cid, s in list(self.sessions.items()):
                    if time.time() - s.last_access > idle or not s.alive():
                        s.stop()
                        del self.sessions[cid]

    def status(self):
        return [{"channel_id": cid, "name": s.channel["name"], "idle": int(time.time() - s.last_access)}
                for cid, s in self.sessions.items()]


live = LiveManager()


# ---------------------------------------------------------------- DVR

def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", s or "").strip()[:80] or "recording"


def _rebuild_playlist(out_dir, segs):
    """Rewrite index.m3u8 when it lost its EXTINF list (e.g. a colliding ffmpeg run truncated
    it) but the segment files are still on disk. Durations are nominal; that is fine for
    sequential playback."""
    names = sorted(f for f in segs if re.fullmatch(r"seg\d+\.ts", f))
    if not names:
        return
    target = int(config.get("hls_segment_seconds")) or 4
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-REBUILT",
             f"#EXT-X-TARGETDURATION:{target + 1}", "#EXT-X-MEDIA-SEQUENCE:0"]
    lines += [l for n in names for l in (f"#EXTINF:{float(target):.6f},", n)]
    lines.append("#EXT-X-ENDLIST")
    with open(os.path.join(out_dir, "index.m3u8"), "w") as f:
        f.write("\n".join(lines) + "\n")
    log.info("rebuilt playlist in %s (%d segments)", out_dir, len(names))


class Recorder:
    def __init__(self):
        self.active = {}   # schedule_id -> (Popen, recording_id)
        self.timeshift = {}  # recording_id -> {"sid", "touch", "keep"}
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        self._recover()
        while True:
            try:
                self._tick()
                self._reap_timeshift()
            except Exception:
                log.exception("recorder tick failed")
            time.sleep(10)

    def _recover(self):
        """Recordings left in 'recording' by a restart/crash: keep what made it to disk."""
        for rec in db.rows("SELECT id FROM recordings WHERE status='recording'"):
            sched = db.row("SELECT id FROM schedules WHERE recording_id=? AND status='recording'", (rec["id"],))
            self._finalize(sched["id"] if sched else None, rec["id"], "recovered")
        db.execute("UPDATE schedules SET status='failed' WHERE status='recording'")

    def _tick(self):
        now = int(time.time())
        pre = config.get("pre_pad_min") * 60
        post = config.get("post_pad_min") * 60
        due = db.rows("SELECT * FROM schedules WHERE status='scheduled' AND start - ? <= ? AND stop + ? > ?",
                      (pre, now, post, now))
        for s in due:
            self._start(s, now, post)
        # Live buffers nobody is watching any more: stop them so they don't fill the SD card.
        idle = config.get("timeshift_idle_seconds")
        with self.lock:
            stale = [(rid, t["sid"]) for rid, t in self.timeshift.items()
                     if not t["keep"] and now - t["touch"] > idle]
            active = list(self.active.items())
        for rid, sid in stale:
            log.info("timeshift idle, stopping rec=%s", rid)
            self.cancel(sid)
        # Never wait on ffmpeg while holding the lock: API requests (touch/ready/playlist)
        # need it and would stall for the whole shutdown.
        for sid, (proc, rid) in active:
            sched = db.row("SELECT * FROM schedules WHERE id=?", (sid,))
            expired = sched is None or now >= sched["stop"] + post or sched["status"] == "cancelled"
            if expired and proc.poll() is None:
                proc.terminate()
            if proc.poll() is not None or expired:
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                with self.lock:
                    owned = self.active.pop(sid, None) is not None
                if owned:
                    self._finish(sid, rid, proc.returncode)

    def _start(self, sched, now, post, timeshift=False):
        ch = db.row("SELECT * FROM channels WHERE id=?", (sched["channel_id"],))
        if not ch:
            db.execute("UPDATE schedules SET status='failed' WHERE id=?", (sched["id"],))
            return
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(sched["start"]))
        # Folder must be unique per schedule: two recordings of the same show started in the
        # same minute would otherwise share a directory, and the second ffmpeg truncates
        # index.m3u8 while the first is still writing it.
        folder = f"{stamp} {_safe_name(sched['title'] or ch['name'])}-s{sched['id']}"
        out_dir = os.path.join(config.get("recordings_dir"), folder)
        os.makedirs(out_dir, exist_ok=True)
        playlist = os.path.join(out_dir, "index.m3u8")
        prog = db.row("SELECT description FROM programs WHERE tvg_id=? AND start<=? AND stop>? ORDER BY start DESC LIMIT 1",
                      (ch["tvg_id"], sched["start"], sched["start"]))
        rid = db.execute(
            "INSERT INTO recordings(channel_id, channel_name, title, description, start, stop, path, status) "
            "VALUES(?,?,?,?,?,?,?, 'recording')",
            (ch["id"], ch["name"], sched["title"] or ch["name"], prog["description"] if prog else "",
             now, sched["stop"] + post, folder))
        duration = max(60, sched["stop"] + post - now)
        # Event-style HLS: playable on Roku while still recording; ENDLIST written on finish.
        # Shorter segments for live buffers so the viewer can join sooner.
        seg = "3" if timeshift else "6"
        cmd = [FFMPEG] + _input_args(ch["url"]) + _copy_args() + [
            "-t", str(duration),
            "-f", "hls", "-hls_time", seg, "-hls_list_size", "0",
            "-hls_playlist_type", "event",
            "-hls_segment_filename", os.path.join(out_dir, "seg%05d.ts"),
            playlist,
        ]
        log.info("record start sched=%s rec=%s '%s'", sched["id"], rid, folder)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(os.path.join(out_dir, "ffmpeg.log"), "ab"))
        with self.lock:
            self.active[sched["id"]] = (proc, rid)
            if timeshift:
                self.timeshift[rid] = {"sid": sched["id"], "touch": now, "keep": False}
        db.execute("UPDATE schedules SET status='recording', recording_id=? WHERE id=?", (rid, sched["id"]))
        return rid

    def _finish(self, sid, rid, rc):
        self._finalize(sid, rid, rc)
        with self.lock:
            self.timeshift.pop(rid, None)

    def _finalize(self, sid, rid, rc):
        rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,))
        out_dir = os.path.join(config.get("recordings_dir"), rec["path"]) if rec else None
        size, ok = 0, False
        if out_dir and os.path.isdir(out_dir):
            segs = [f for f in os.listdir(out_dir) if f.endswith(".ts")]
            size = sum(os.path.getsize(os.path.join(out_dir, f)) for f in segs)
            ok = len(segs) > 0
            pl = os.path.join(out_dir, "index.m3u8")
            if ok and os.path.exists(pl):
                text = open(pl).read()
                if "#EXTINF" not in text:
                    _rebuild_playlist(out_dir, segs)
                elif "#EXT-X-ENDLIST" not in text:
                    with open(pl, "a") as f:
                        f.write("#EXT-X-ENDLIST\n")
        status = "done" if ok else "failed"
        log.info("record finish sched=%s rec=%s rc=%s status=%s size=%d", sid, rid, rc, status, size)
        db.execute("UPDATE recordings SET status=?, size_bytes=?, stop=? WHERE id=?", (status, size, int(time.time()), rid))
        if sid is not None:
            db.execute("UPDATE schedules SET status=? WHERE id=? AND status != 'cancelled'", (status, sid))

    def cancel(self, sid):
        db.execute("UPDATE schedules SET status='cancelled' WHERE id=? AND status IN ('scheduled','recording')", (sid,))
        with self.lock:
            entry = self.active.get(sid)
        if entry and entry[0].poll() is None:
            entry[0].terminate()

    def start_now(self, cid, start, stop, title="Timeshift"):
        """Create and immediately start a live-buffer recording for the current show."""
        now = int(time.time())
        sid = db.execute(
            "INSERT INTO schedules(channel_id,title,start,stop,status,created) VALUES(?,?,?,?,'scheduled',?)",
            (cid, f"[timeshift] {title}", start, stop, now))
        sched = db.row("SELECT * FROM schedules WHERE id=?", (sid,))
        return self._start(sched, now, 0, timeshift=True)

    def active_timeshift(self, cid):
        """recording_id of a live buffer still running for this channel, or None."""
        with self.lock:
            rids = list(self.timeshift)
        for rid in rids:
            rec = db.row("SELECT id FROM recordings WHERE id=? AND channel_id=? AND status='recording'", (rid, cid))
            if rec:
                return rid
        return None

    def reuse_timeshift(self, rid, stop):
        """Restart a stopped live buffer in place: same recordings row and folder, so flipping
        back to a channel doesn't pile up duplicate entries for the same show."""
        rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,))
        if not rec or rec["status"] == "recording":
            return None
        ch = db.row("SELECT * FROM channels WHERE id=?", (rec["channel_id"],))
        if not ch:
            return None
        now = int(time.time())
        out_dir = os.path.join(config.get("recordings_dir"), rec["path"])
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        playlist = os.path.join(out_dir, "index.m3u8")
        duration = max(60, stop - now)
        cmd = [FFMPEG] + _input_args(ch["url"]) + _copy_args() + [
            "-t", str(duration),
            "-f", "hls", "-hls_time", "3", "-hls_list_size", "0",
            "-hls_playlist_type", "event",
            "-hls_segment_filename", os.path.join(out_dir, "seg%05d.ts"),
            playlist,
        ]
        sid = db.execute(
            "INSERT INTO schedules(channel_id,title,start,stop,status,created,recording_id) VALUES(?,?,?,?,'recording',?,?)",
            (rec["channel_id"], rec["title"], now, stop, now, rid))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=open(os.path.join(out_dir, "ffmpeg.log"), "ab"))
        with self.lock:
            self.active[sid] = (proc, rid)
            self.timeshift[rid] = {"sid": sid, "touch": now, "keep": False}
        db.execute("UPDATE recordings SET status='recording', start=?, stop=?, size_bytes=0 WHERE id=?",
                   (now, stop, rid))
        log.info("timeshift restart in place rec=%s", rid)
        return rid

    def touch_timeshift(self, rid):
        with self.lock:
            t = self.timeshift.get(rid)
            if t:
                t["touch"] = int(time.time())
            return t is not None

    def stop_other_timeshifts(self):
        """Stop every un-kept live buffer now (viewer changed channel); finish them off-thread."""
        with self.lock:
            victims = [(rid, t["sid"]) for rid, t in self.timeshift.items() if not t["keep"]]
            entries = [(sid, self.active.pop(sid, None), rid) for rid, sid in victims]
            for rid, _ in victims:
                self.timeshift.pop(rid, None)
        for sid, entry, rid in entries:
            db.execute("UPDATE schedules SET status='cancelled' WHERE id=? AND status IN ('scheduled','recording')", (sid,))
            if entry is None:
                continue
            proc = entry[0]
            if proc.poll() is None:
                proc.terminate()

            def _reap(p=proc, s=sid, r=rid):
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                self._finish(s, r, p.returncode)
            threading.Thread(target=_reap, daemon=True).start()

    def keep_timeshift(self, rid):
        with self.lock:
            t = self.timeshift.get(rid)
            if t:
                t["keep"] = True

    def _reap_timeshift(self):
        """Delete finished live buffers that were not kept."""
        cutoff = int(time.time()) - config.get("timeshift_keep_hours") * 3600
        for r in db.rows("SELECT * FROM recordings WHERE status IN ('done','failed') "
                         "AND title LIKE '[timeshift] %' AND stop < ?", (cutoff,)):
            delete_recording(r["id"])

    def last_error(self, rid):
        """Last non-empty line of the recording's ffmpeg.log (why it failed), or ''."""
        rec = db.row("SELECT path FROM recordings WHERE id=?", (rid,))
        if not rec:
            return ""
        try:
            with open(os.path.join(config.get("recordings_dir"), rec["path"], "ffmpeg.log"), errors="replace") as f:
                lines = [l.strip() for l in f.read()[-4000:].splitlines() if l.strip()]
        except OSError:
            return ""
        return lines[-1] if lines else ""

    def segments(self, rid):
        """(segment count, playlist finished?) for a recording's HLS playlist."""
        rec = db.row("SELECT path, status FROM recordings WHERE id=?", (rid,))
        if not rec:
            return 0, False
        out_dir = os.path.join(config.get("recordings_dir"), rec["path"])
        pl = os.path.join(out_dir, "index.m3u8")
        try:
            with open(pl) as f:
                text = f.read()
        except OSError:
            return 0, False
        if "#EXTINF" not in text and rec["status"] != "recording" and "#EXT-X-REBUILT" not in text:
            segs = [f for f in os.listdir(out_dir) if f.endswith(".ts")]
            if segs:
                _rebuild_playlist(out_dir, segs)
                text = open(pl).read()
        return text.count("#EXTINF"), "#EXT-X-ENDLIST" in text


recorder = Recorder()


def start():
    live.start()
    recorder.start()


def delete_recording(rid):
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,))
    if not rec:
        return False
    if rec["status"] == "recording":
        s = db.row("SELECT id FROM schedules WHERE recording_id=?", (rid,))
        if s:
            recorder.cancel(s["id"])
            time.sleep(1)
    shutil.rmtree(os.path.join(config.get("recordings_dir"), rec["path"]), ignore_errors=True)
    db.execute("DELETE FROM recordings WHERE id=?", (rid,))
    return True
