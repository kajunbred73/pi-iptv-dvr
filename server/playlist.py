"""M3U playlist + XMLTV EPG import."""
import gzip
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import config
import db

log = logging.getLogger("playlist")

_ATTR = re.compile(r'([a-zA-Z0-9\-_]+)="([^"]*)"')


def _fetch(url):
    if url.startswith("file://"):
        with open(url[7:], "rb") as f:
            return f.read()
    r = requests.get(url, timeout=60, headers={"User-Agent": config.get("user_agent")})
    r.raise_for_status()
    data = r.content
    if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def parse_m3u(text):
    channels = []
    pending = None
    for raw in text.splitlines():
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
            channels.append(pending)
            pending = None
    return channels


def import_m3u(url=None):
    url = url or config.get("m3u_url")
    if not url:
        return 0
    chans = parse_m3u(_fetch(url).decode("utf-8", "replace"))
    c = db.conn()
    favs = {r["url"]: r["favorite"] for r in db.rows("SELECT url, favorite FROM channels")}
    c.execute("DELETE FROM channels")
    for i, ch in enumerate(chans, 1):
        c.execute(
            "INSERT INTO channels(num, name, tvg_id, logo, grp, url, favorite) VALUES(?,?,?,?,?,?,?)",
            (ch["num"] or i, ch["name"], ch["tvg_id"], ch["logo"], ch["grp"], ch["url"], favs.get(ch["url"], 0)),
        )
    c.commit()
    db.set_meta("m3u_last", int(time.time()))
    log.info("imported %d channels", len(chans))
    return len(chans)


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


def import_epg(url=None):
    url = url or config.get("epg_url")
    if not url:
        return 0
    data = _fetch(url)
    wanted = {r["tvg_id"] for r in db.rows("SELECT DISTINCT tvg_id FROM channels WHERE tvg_id != ''")}
    cutoff = int(time.time()) - 6 * 3600
    count = 0
    c = db.conn()
    c.execute("DELETE FROM programs")
    batch = []
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != "programme":
            continue
        ch = el.get("channel", "")
        if wanted and ch not in wanted:
            el.clear()
            continue
        start, stop = _xmltv_time(el.get("start", "")), _xmltv_time(el.get("stop", ""))
        if start and stop and stop > cutoff:
            batch.append((
                ch, start, stop,
                (el.findtext("title") or "").strip(),
                (el.findtext("desc") or "").strip(),
                (el.findtext("category") or "").strip(),
            ))
            count += 1
        el.clear()
        if len(batch) >= 2000:
            c.executemany("INSERT INTO programs(tvg_id,start,stop,title,description,category) VALUES(?,?,?,?,?,?)", batch)
            batch = []
    if batch:
        c.executemany("INSERT INTO programs(tvg_id,start,stop,title,description,category) VALUES(?,?,?,?,?,?)", batch)
    c.commit()
    db.set_meta("epg_last", int(time.time()))
    log.info("imported %d programs", count)
    return count


def refresh_all():
    result = {}
    try:
        result["channels"] = import_m3u()
    except Exception as e:
        log.exception("m3u import failed")
        result["channels_error"] = str(e)
    try:
        result["programs"] = import_epg()
    except Exception as e:
        log.exception("epg import failed")
        result["programs_error"] = str(e)
    return result
