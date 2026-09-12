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


def _input_args(url):
    args = ["-hide_banner", "-loglevel", "warning", "-nostdin",
            "-fflags", "+genpts+discardcorrupt"]
    if url.startswith("http"):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                 "-user_agent", config.get("user_agent")]
    else:
        args += ["-re"]  # local files: read in real time
    args += ["-i", url]
    return args


def _copy_args():
    # Remux only (no transcoding) so a Pi can keep up. Roku plays H.264/AAC HLS,
    # which is what nearly all IPTV sources already are.
    return ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-sn", "-dn"]


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


class Recorder:
    def __init__(self):
        self.active = {}   # schedule_id -> (Popen, recording_id)
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        db.execute("UPDATE recordings SET status='failed' WHERE status='recording'")
        db.execute("UPDATE schedules SET status='failed' WHERE status='recording'")
        while True:
            try:
                self._tick()
            except Exception:
                log.exception("recorder tick failed")
            time.sleep(10)

    def _tick(self):
        now = int(time.time())
        pre = config.get("pre_pad_min") * 60
        post = config.get("post_pad_min") * 60
        due = db.rows("SELECT * FROM schedules WHERE status='scheduled' AND start - ? <= ? AND stop + ? > ?",
                      (pre, now, post, now))
        for s in due:
            self._start(s, now, post)
        with self.lock:
            for sid, (proc, rid) in list(self.active.items()):
                sched = db.row("SELECT * FROM schedules WHERE id=?", (sid,))
                expired = sched is None or now >= sched["stop"] + post or sched["status"] == "cancelled"
                if expired and proc.poll() is None:
                    proc.terminate()
                if proc.poll() is not None or expired:
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    self._finish(sid, rid, proc.returncode)
                    del self.active[sid]

    def _start(self, sched, now, post):
        ch = db.row("SELECT * FROM channels WHERE id=?", (sched["channel_id"],))
        if not ch:
            db.execute("UPDATE schedules SET status='failed' WHERE id=?", (sched["id"],))
            return
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(sched["start"]))
        folder = f"{stamp} {_safe_name(sched['title'] or ch['name'])}"
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
        cmd = [FFMPEG] + _input_args(ch["url"]) + _copy_args() + [
            "-t", str(duration),
            "-f", "hls", "-hls_time", "6", "-hls_list_size", "0",
            "-hls_playlist_type", "event",
            "-hls_segment_filename", os.path.join(out_dir, "seg%05d.ts"),
            playlist,
        ]
        log.info("record start sched=%s rec=%s '%s'", sched["id"], rid, folder)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(os.path.join(out_dir, "ffmpeg.log"), "ab"))
        with self.lock:
            self.active[sched["id"]] = (proc, rid)
        db.execute("UPDATE schedules SET status='recording', recording_id=? WHERE id=?", (rid, sched["id"]))

    def _finish(self, sid, rid, rc):
        rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,))
        out_dir = os.path.join(config.get("recordings_dir"), rec["path"]) if rec else None
        size, ok = 0, False
        if out_dir and os.path.isdir(out_dir):
            segs = [f for f in os.listdir(out_dir) if f.endswith(".ts")]
            size = sum(os.path.getsize(os.path.join(out_dir, f)) for f in segs)
            ok = len(segs) > 0
            pl = os.path.join(out_dir, "index.m3u8")
            if ok and os.path.exists(pl) and "#EXT-X-ENDLIST" not in open(pl).read():
                with open(pl, "a") as f:
                    f.write("#EXT-X-ENDLIST\n")
        status = "done" if ok else "failed"
        log.info("record finish sched=%s rec=%s rc=%s status=%s size=%d", sid, rid, rc, status, size)
        db.execute("UPDATE recordings SET status=?, size_bytes=?, stop=? WHERE id=?", (status, size, int(time.time()), rid))
        db.execute("UPDATE schedules SET status=? WHERE id=? AND status != 'cancelled'", (status, sid))

    def cancel(self, sid):
        db.execute("UPDATE schedules SET status='cancelled' WHERE id=? AND status IN ('scheduled','recording')", (sid,))
        with self.lock:
            entry = self.active.get(sid)
        if entry and entry[0].poll() is None:
            entry[0].terminate()


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
