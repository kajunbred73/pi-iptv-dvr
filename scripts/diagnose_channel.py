#!/usr/bin/env python3
"""Diagnose why specific channels fail to play/record.

Run from the repo root on the Pi:

    python3 scripts/diagnose_channel.py "FOX 26" "TV One"

Each argument is a name filter (case-insensitive substring). With no arguments it
just inspects the 3 most recent recordings. Prints channel DB info, an ffprobe of
the stream URL, and the ffmpeg log + playlist state for the newest recording
folders so the output can be screenshotted/emailed back.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("IPTV_DATA_DIR", os.path.join(ROOT, "server", "data"))
DB = os.path.join(DATA_DIR, "iptv.db")
REC_DIR = os.path.join(DATA_DIR, "recordings")

# config.json can relocate recordings_dir
cfg_path = os.path.join(DATA_DIR, "config.json")
if os.path.exists(cfg_path):
    try:
        REC_DIR = json.load(open(cfg_path)).get("recordings_dir") or REC_DIR
    except Exception:
        pass


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe_channel(ch_id, name, url):
    hr(f"Channel {ch_id}: {name}")
    print(f"URL: {url}")
    print("\n-- ffprobe (20s timeout) --")
    try:
        out = subprocess.run(
            ["ffprobe", "-hide_banner", "-v", "info", url],
            capture_output=True, text=True, timeout=25)
        lines = [l.rstrip() for l in (out.stderr + out.stdout).splitlines()
                 if any(k in l for k in ("Stream", "Duration", "Input", "error", "Error", "timed out"))]
        print("\n".join(lines[:25]) or "(no output)")
    except FileNotFoundError:
        print("ffprobe not found")
    except subprocess.TimeoutExpired:
        print("ffprobe TIMED OUT after 25s - stream is not answering")

    # Same ffmpeg args streamer.py uses for recordings.
    print("\n-- ffmpeg record test (up to 150s, Ctrl+C to skip) --")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
           "-fflags", "+genpts+discardcorrupt+igndts", "-err_detect", "ignore_err",
           "-analyzeduration", "3000000", "-probesize", "5000000",
           "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
           "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5",
           "-user_agent", "VLC/3.0.20 LibVLC/3.0.20",
           "-i", url, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
           "-sn", "-dn", "-f", "null", "-"]
    try:
        t0 = time.time()
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        ran = int(time.time() - t0)
        print(f"ffmpeg EXITED by itself after {ran}s (rc={out.returncode})  <-- PROBLEM")
    except subprocess.TimeoutExpired:
        print("ffmpeg still running at 150s - stream is fine, problem is elsewhere")
        return
    lines = [l.rstrip() for l in out.stderr.splitlines() if l.strip()]
    print("\n".join(lines[-15:]))


def show_recording(folder):
    hr(f"Recording folder: {folder}")
    path = os.path.join(REC_DIR, folder)
    segs = [f for f in os.listdir(path) if f.endswith(".ts")]
    print(f"{len(segs)} segment files")
    pl = os.path.join(path, "index.m3u8")
    if os.path.exists(pl):
        text = open(pl).read()
        print(f"playlist: {text.count('#EXTINF')} EXTINF, "
              f"ENDLIST={'yes' if '#EXT-X-ENDLIST' in text else 'no'}, "
              f"age={int(time.time() - os.path.getmtime(pl))}s")
        print("-- playlist tail --")
        print("\n".join(text.splitlines()[-8:]))
    else:
        print("no index.m3u8")
    log = os.path.join(path, "ffmpeg.log")
    if os.path.exists(log):
        print("-- ffmpeg.log tail --")
        lines = [l.rstrip() for l in open(log, errors="replace").read().splitlines() if l.strip()]
        print("\n".join(lines[-20:]) or "(empty)")


def main():
    print(f"DB: {DB}  exists={os.path.exists(DB)}")
    print(f"recordings: {REC_DIR}  exists={os.path.isdir(REC_DIR)}")
    if not os.path.exists(DB):
        print("\nDB not found. If the service runs with IPTV_DATA_DIR set, run:")
        print("  systemctl cat pi-iptv-dvr | grep -i environment")
        return

    filters = [a.lower() for a in sys.argv[1:]]
    con = sqlite3.connect(DB)

    if filters:
        seen = set()
        for f in filters:
            for ch_id, name, url in con.execute(
                    "SELECT id, name, url FROM channels WHERE lower(name) LIKE ? ORDER BY name",
                    (f"%{f}%",)):
                if ch_id in seen:
                    continue
                seen.add(ch_id)
                probe_channel(ch_id, name, url)
        if not seen:
            print("No channels matched. Try a shorter filter, e.g. 'fox' or 'one'.")

    hr("Most recent recordings")
    recs = con.execute(
        "SELECT r.id, r.channel_name, r.title, r.status, r.path "
        "FROM recordings r ORDER BY r.id DESC LIMIT 3").fetchall()
    for rid, ch, title, status, path in recs:
        print(f"#{rid} [{status}] {ch} - {title}")
        if os.path.isdir(os.path.join(REC_DIR, path)):
            show_recording(path)
        else:
            print(f"  folder missing: {path}")

    print("\nDone. Screenshot or paste this output back.")


if __name__ == "__main__":
    main()
