"""M3U playlist + XMLTV EPG import.

Designed for a Raspberry Pi: downloads stream to disk and the XMLTV is parsed
incrementally, so memory stays flat even for multi-hundred-MB guide files.
"""
import gzip
import logging
import os
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import config
import db

log = logging.getLogger("playlist")

_ATTR = re.compile(r'([a-zA-Z0-9\-_]+)="([^"]*)"')
_lock = threading.Lock()
state = {"running": False, "step": "", "started": 0, "finished": 0, "result": {}}


def _download(url, dest):
    if url.startswith("file://"):
        shutil.copyfile(url[7:], dest)
        return dest
    with requests.get(url, timeout=(30, 120), stream=True,
                      headers={"User-Agent": config.get("user_agent")}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                f.write(chunk)
    return dest


def _open_maybe_gzip(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def _tmp(name):
    d = os.path.join(config.DATA_DIR, "tmp")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


# ---------------------------------------------------------------- M3U

def parse_m3u_lines(lines):
    pending = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(_ATTR.findall(line))
            name = line.rsplit(",", 1)[-1].strip() if "," in line else attrs.get("tvg-name", "")
            pending = {
                "name": name or attrs.get("tvg-name") or "Unknown",
                "tvg_id": attrs.get("tvg-id", ""),
                "logo": attrs.get("tvg-logo", ""),
                "grp": attrs.get("group-title", ""),
                "num": int(attrs["tvg-chno"]) if attrs.get("tvg-chno", "").isdigit() else None,
            }
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending["url"] = line
            yield pending
            pending = None


def parse_m3u(text):
    return list(parse_m3u_lines(text.splitlines()))


def import_m3u(url=None):
    url = url or config.get("m3u_url")
    if not url:
        return 0
    path = _download(url, _tmp("playlist.m3u"))
    c = db.conn()
    favs = {r["url"]: r["favorite"] for r in db.rows("SELECT url, favorite FROM channels")}
    count = 0
    with _open_maybe_gzip(path) as fh:
        lines = (l.decode("utf-8", "replace") for l in fh)
        c.execute("DELETE FROM channels")
        for i, ch in enumerate(parse_m3u_lines(lines), 1):
            c.execute(
                "INSERT INTO channels(num, name, tvg_id, logo, grp, url, favorite) VALUES(?,?,?,?,?,?,?)",
                (ch["num"] or i, ch["name"], ch["tvg_id"], ch["logo"], ch["grp"], ch["url"], favs.get(ch["url"], 0)),
            )
            count += 1
        c.commit()
    db.set_meta("m3u_last", int(time.time()))
    log.info("imported %d channels", count)
    return count


# ---------------------------------------------------------------- XMLTV

def _xmltv_time(s):
    # 20240101120000 +0000
    s = s.strip()
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*([+-]\d{4})?", s)
    if not m:
        return None
    y, mo, d, h, mi, se, tz = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0), tzinfo=timezone.utc)
    if tz:
        sign = 1 if tz[0] == "+" else -1
        off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        dt = dt - off
    return int(dt.timestamp())


def import_epg(url=None, max_days=3):
    url = url or config.get("epg_url")
    if not url:
        return 0
    path = _download(url, _tmp("epg.xml"))
    wanted = {r["tvg_id"] for r in db.rows("SELECT DISTINCT tvg_id FROM channels WHERE tvg_id != ''")}
    now = int(time.time())
    cutoff_lo, cutoff_hi = now - 6 * 3600, now + max_days * 86400
    count = 0
    c = db.conn()
    c.execute("DELETE FROM programs")
    batch = []
    root = None
    with _open_maybe_gzip(path) as fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = el
                continue
            if el.tag != "programme":
                if el.tag == "channel":
                    el.clear()
                continue
            ch = el.get("channel", "")
            if not wanted or ch in wanted:
                start, stop = _xmltv_time(el.get("start", "")), _xmltv_time(el.get("stop", ""))
                if start and stop and stop > cutoff_lo and start < cutoff_hi:
                    batch.append((
                        ch, start, stop,
                        (el.findtext("title") or "").strip(),
                        (el.findtext("desc") or "").strip()[:1000],
                        (el.findtext("category") or "").strip(),
                    ))
                    count += 1
            el.clear()
            if len(batch) >= 2000:
                c.executemany("INSERT INTO programs(tvg_id,start,stop,title,description,category) VALUES(?,?,?,?,?,?)", batch)
                batch = []
                root.clear()
    if batch:
        c.executemany("INSERT INTO programs(tvg_id,start,stop,title,description,category) VALUES(?,?,?,?,?,?)", batch)
    c.commit()
    db.set_meta("epg_last", int(time.time()))
    log.info("imported %d programs", count)
    return count


# ---------------------------------------------------------------- orchestration

def refresh_all():
    """Synchronous import of playlist then EPG. Returns a summary dict."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    result = {}
    state.update(running=True, started=int(time.time()), finished=0, result={})
    try:
        state["step"] = "playlist"
        try:
            result["channels"] = import_m3u()
        except Exception as e:
            log.exception("m3u import failed")
            result["channels_error"] = str(e)
        state["step"] = "guide"
        try:
            result["programs"] = import_epg()
        except Exception as e:
            log.exception("epg import failed")
            result["programs_error"] = str(e)
    finally:
        state.update(running=False, step="", finished=int(time.time()), result=result)
        shutil.rmtree(os.path.join(config.DATA_DIR, "tmp"), ignore_errors=True)
        _lock.release()
    return result


def refresh_async():
    """Kick off refresh_all in the background; returns immediately."""
    if state["running"]:
        return False
    threading.Thread(target=refresh_all, daemon=True).start()
    return True
