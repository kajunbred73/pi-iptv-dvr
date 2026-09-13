"""M3U playlist + XMLTV EPG import.

Designed for a Raspberry Pi: downloads stream to disk and the XMLTV is parsed
incrementally, so memory stays flat even for multi-hundred-MB guide files.
"""
import gzip
import json
import logging
import os
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
from urllib.parse import quote

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


_VOD = re.compile(r"/(movie|series)/|\.(mp4|mkv|avi|mov|flv|wmv)(\?|$)", re.I)


def _is_live(url):
    return not _VOD.search(url)


def _m3u_channels():
    path = _download(config.get("m3u_url"), _tmp("playlist.m3u"))
    with _open_maybe_gzip(path) as fh:
        lines = (l.decode("utf-8", "replace") for l in fh)
        for ch in parse_m3u_lines(lines):
            if _is_live(ch["url"]):
                yield ch


def _xtream_channels():
    """Live channels via the Xtream player_api (a few MB) instead of the full
    M3U, which on most providers also lists every movie/series."""
    host, user, pw = config.get("xtream_host"), config.get("xtream_user"), config.get("xtream_pass")
    base = f"{host}/player_api.php?username={quote(user)}&password={quote(pw)}"
    with open(_download(base + "&action=get_live_categories", _tmp("cats.json")), "rb") as f:
        cats = {str(c.get("category_id")): c.get("category_name", "") for c in (json.load(f) or [])}
    with open(_download(base + "&action=get_live_streams", _tmp("live.json")), "rb") as f:
        streams = json.load(f) or []
    for s in streams:
        sid = s.get("stream_id")
        if sid is None:
            continue
        num = s.get("num")
        yield {
            "name": s.get("name") or "Unknown",
            "tvg_id": s.get("epg_channel_id") or "",
            "logo": s.get("stream_icon") or "",
            "grp": cats.get(str(s.get("category_id")), ""),
            "num": int(num) if isinstance(num, int) or str(num).isdigit() else None,
            "url": f"{host}/live/{quote(user)}/{quote(pw)}/{sid}.ts",
        }


def import_m3u(url=None):
    xtream = all(config.get(k) for k in ("xtream_host", "xtream_user", "xtream_pass"))
    if not xtream and not (url or config.get("m3u_url")):
        return 0
    if url:
        config.save({"m3u_url": url})
    source = _xtream_channels() if xtream else _m3u_channels()
    c = db.conn()
    favs = {r["url"]: r["favorite"] for r in db.rows("SELECT url, favorite FROM channels")}
    count = 0
    groups = {}
    c.execute("DELETE FROM channels")
    for i, ch in enumerate(source, 1):
        c.execute(
            "INSERT INTO channels(num, name, tvg_id, logo, grp, url, favorite) VALUES(?,?,?,?,?,?,?)",
            (ch["num"] or i, ch["name"], ch["tvg_id"], ch["logo"], ch["grp"], ch["url"], favs.get(ch["url"], 0)),
        )
        groups[ch["grp"]] = groups.get(ch["grp"], 0) + 1
        count += 1
    # New groups start enabled only for small playlists; otherwise the user picks in Settings.
    default_on = 1 if count <= 500 else 0
    c.executemany("INSERT OR IGNORE INTO groups(name, enabled) VALUES(?,?)", [(g, default_on) for g in groups])
    c.executemany("UPDATE groups SET count=? WHERE name=?", [(n, g) for g, n in groups.items()])
    if groups:
        c.execute("DELETE FROM groups WHERE name NOT IN (%s)" % ",".join("?" * len(groups)), list(groups))
    c.commit()
    db.set_meta("m3u_last", int(time.time()))
    log.info("imported %d live channels in %d groups", count, len(groups))
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
    wanted = {r["tvg_id"] for r in db.rows(
        "SELECT DISTINCT tvg_id FROM channels WHERE tvg_id != '' AND grp IN (SELECT name FROM groups WHERE enabled=1)")}
    if not wanted:
        log.info("no enabled groups with EPG ids; skipping guide import")
        return 0
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

def refresh_all(epg_only=False):
    """Synchronous import of playlist then EPG. Returns a summary dict."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    result = {}
    state.update(running=True, started=int(time.time()), finished=0, result={})
    try:
        if not epg_only:
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


def refresh_async(epg_only=False):
    """Kick off refresh_all in the background; returns immediately."""
    if state["running"]:
        return False
    threading.Thread(target=refresh_all, args=(epg_only,), daemon=True).start()
    return True
