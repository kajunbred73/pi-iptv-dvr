"""Trick-play BIF archives built from the muxed HLS segments on disk.

The Roku shows these thumbnails in its built-in seek bar during FF/RW when the
ContentNode carries sdbifurl/hdbifurl. Instead of decoding whole files we grab
the first frame of chosen segments - HLS segments start on keyframes, so a
single-frame read is near-instant - and pack the JPEGs into the BIF container.

BIF layout (all little-endian, per Roku's spec):
  8B  magic 89 42 49 46 0D 0A 1A 0A
  4B  version (0)
  4B  image count N
  4B  timestamp multiplier (ms per timestamp unit; we write 1000)
  44B reserved zeros
  (N+1) x 8B index: {timestamp units, absolute jpeg offset}; final entry is
      {0xFFFFFFFF, offset just past the last jpeg}
  jpeg payloads, adjacent and in index order
"""
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

MAGIC = b"\x89BIF\r\n\x1a\n"
TW, TH = 320, 240            # Roku "HD" bif thumbnail size
SPACING = 10.0               # seconds of content per thumbnail
MAX_THUMBS = 1200            # spacing widens if the content would exceed this
BATCH = 40                   # segments per ffmpeg invocation
REBUILD_GAP = 60             # rebuild once uncovered tail passes this many seconds

_VF = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
       "pad=%d:%d:(ow-iw)/2:(oh-ih)/2") % (TW, TH, TW, TH)

_lock = threading.Lock()
_jobs = set()                # seg dirs with a builder thread running


def _segments(playlist):
    """[(seg filename, content-offset seconds)] + total duration, or None."""
    try:
        with open(playlist) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    segs, cum, dur = [], 0.0, 0.0
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                dur = float(line[8:].split(",")[0])
            except ValueError:
                dur = 0.0
        elif line and not line.startswith("#"):
            segs.append((line, cum))
            cum += dur
            dur = 0.0
    return segs, cum


def _meta_path(seg_dir):
    return os.path.join(seg_dir, "bif.json")


def _load_meta(seg_dir):
    try:
        with open(_meta_path(seg_dir)) as f:
            m = json.load(f)
        if isinstance(m.get("items"), dict) and isinstance(m.get("bad"), list):
            return m
    except (OSError, ValueError):
        pass
    return {"covered": 0.0, "items": {}, "bad": []}


def _grab_batch(jobs, out_dir):
    """jobs: [(seg path, out name)]. One ffmpeg pass, one -map/-frames:v per file."""
    if not jobs:
        return
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    for src, _ in jobs:
        cmd += ["-i", src]
    for i, (_, name) in enumerate(jobs):
        # ?: an audio-only or corrupt segment must not kill the whole batch.
        cmd += ["-map", f"{i}:v:0?", "-frames:v", "1", "-vf", _VF,
                "-q:v", "5", os.path.join(out_dir, name)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _pack(seg_dir, items):
    """Atomically (re)write trickplay.bif from [(ts_ms, jpg path)]."""
    entries = sorted((t, p) for t, p in items if p and os.path.exists(p))
    if not entries:
        return
    bif = os.path.join(seg_dir, "trickplay.bif")
    tmp = bif + ".tmp"
    data_off = 64 + (len(entries) + 1) * 8
    index, blobs, off, prev_ts = [], [], data_off, -1
    for ts_ms, path in entries:
        with open(path, "rb") as f:
            blob = f.read()
        if blob[:2] != b"\xff\xd8":
            continue
        ts_u = max(prev_ts + 1, int(round(ts_ms / 1000.0)))   # strictly increasing
        prev_ts = ts_u
        index.append((ts_u, off))
        blobs.append(blob)
        off += len(blob)
    if not blobs:
        return
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<III", 0, len(blobs), 1000))
        f.write(b"\x00" * 44)
        for ts_u, o in index:
            f.write(struct.pack("<II", ts_u, o))
        f.write(struct.pack("<II", 0xFFFFFFFF, off))
        for blob in blobs:
            f.write(blob)
    os.replace(tmp, bif)


def _build(seg_dir, playlist):
    try:
        tdir = os.path.join(seg_dir, "thumbs")
        os.makedirs(tdir, exist_ok=True)
        meta = _load_meta(seg_dir)
        found = _segments(playlist)
        if not found:
            return
        segs, total = found
        # The last listed segment is still being written; and only take every
        # Nth segment to hit SPACING (hls_time is 3-6 s).
        segs = segs[:-1] or segs
        if not segs:
            return
        avg = total / max(1, len(segs) + 1)
        step = max(1, round(SPACING / max(avg, 0.5)))
        while len(segs) / step > MAX_THUMBS:
            step += 1
        todo = [(name, ts) for name, ts in segs[::step]
                if name not in meta["items"] and name not in meta["bad"]
                and os.path.exists(os.path.join(seg_dir, name))]
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            jobs = [(os.path.join(seg_dir, name), f"t{int(ts)}_{name}.jpg")
                    for name, ts in chunk]
            _grab_batch(jobs, tdir)
            for (name, ts), (_, out) in zip(chunk, jobs):
                if os.path.exists(os.path.join(tdir, out)):
                    meta["items"][name] = (ts, out)
                else:
                    meta["bad"].append(name)
            # Repack per batch: a partial BIF is useful while the rest builds.
            _pack(seg_dir,
                  [(ts * 1000.0, os.path.join(tdir, out))
                   for ts, out in meta["items"].values()])
        meta["covered"] = total
        try:
            with open(_meta_path(seg_dir), "w") as f:
                json.dump(meta, f)
        except OSError:
            pass
    finally:
        with _lock:
            _jobs.discard(seg_dir)


def ensure_bif(seg_dir, playlist):
    """Kick a background (re)build if the BIF is missing or the content grew past
    the thumbed region. Returns the bif path if one exists right now, else None."""
    bif = os.path.join(seg_dir, "trickplay.bif")
    found = _segments(playlist)
    if not found or not found[0]:
        return bif if os.path.exists(bif) else None
    _, total = found
    covered = _load_meta(seg_dir).get("covered", 0.0)
    need = not os.path.exists(bif) or total - covered > REBUILD_GAP
    if need:
        with _lock:
            if seg_dir not in _jobs:
                _jobs.add(seg_dir)
                threading.Thread(target=_build, args=(seg_dir, playlist),
                                 daemon=True).start()
    return bif if os.path.exists(bif) else None


def wait_for(seg_dir, timeout=12.0):
    """Poll for a built BIF (a build just kicked off). Returns path or None."""
    bif = os.path.join(seg_dir, "trickplay.bif")
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(bif):
            return bif
        with _lock:
            building = seg_dir in _jobs
        if not building and os.path.exists(bif):
            return bif
        time.sleep(0.5)
    return bif if os.path.exists(bif) else None
