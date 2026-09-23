import argparse
import json
import logging
import os
import re
import threading
import time
import urllib.request

from urllib.parse import quote

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

import config
import db
import playlist
import streamer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)
db.init()
streamer.start()


@app.after_request
def _cors(resp):
    # The packaged Tizen app (file:// origin) and browser clients on other
    # devices call this API cross-origin.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ---------------------------------------------------------------- helpers

def _now():
    return int(time.time())


def _base_url():
    return request.url_root.rstrip("/")


_ENABLED = "WHERE grp IN (SELECT name FROM groups WHERE enabled=1)"


def _channel_filter():
    """WHERE clause from ?group= / ?favorites=1 / ?q= (all within enabled groups)."""
    clauses, args = [_ENABLED[6:]], []
    if request.args.get("group"):
        clauses.append("grp=?")
        args.append(request.args["group"])
    if request.args.get("favorites") == "1":
        clauses.append("favorite=1")
    if request.args.get("q"):
        clauses.append("name LIKE ?")
        args.append("%" + request.args["q"] + "%")
    return "WHERE " + " AND ".join(clauses), args


def _channel_json(ch):
    ch = dict(ch)
    ch["stream_url"] = f"{_base_url()}/live/{ch['id']}/index.m3u8"
    return ch


def _now_next(tvg_id, now):
    if not tvg_id:
        return None, None
    progs = db.rows("SELECT * FROM programs WHERE tvg_id=? AND stop>? ORDER BY start LIMIT 2", (tvg_id, now))
    cur = progs[0] if progs and progs[0]["start"] <= now else None
    nxt = progs[1] if cur and len(progs) > 1 else (progs[0] if progs and not cur else None)
    return cur, nxt


def _now_next_all(now):
    """{tvg_id: (current, next)} for every channel in two queries."""
    cur = {p["tvg_id"]: p for p in db.rows(
        "SELECT * FROM programs WHERE start<=? AND stop>?", (now, now))}
    nxt = {p["tvg_id"]: p for p in db.rows(
        "SELECT p.* FROM programs p JOIN (SELECT tvg_id, MIN(start) s FROM programs WHERE start>? GROUP BY tvg_id) m "
        "ON m.tvg_id=p.tvg_id AND m.s=p.start", (now,))}
    return cur, nxt


def _refresh_loop():
    while True:
        last = int(db.get_meta("m3u_last", 0) or 0)
        if config.get("m3u_url") and _now() - last > config.get("refresh_hours") * 3600:
            playlist.refresh_all()
        time.sleep(300)


threading.Thread(target=_refresh_loop, daemon=True).start()


def _roku_launch(ip, why):
    try:
        urllib.request.urlopen(f"http://{ip}:8060/launch/dev", data=b"", timeout=3)
        log.info("Roku autolaunch fired (%s)", why)
        return True
    except Exception as e:
        log.warning("Roku autolaunch failed: %s", e)
        return False


def _roku_watch_loop():
    """Autolaunch the sideloaded app when the Roku (re)boots or powers on.
    Two signals: device-info uptime near zero (restart), or the box coming back
    online after >60s unreachable (Rokus powered from a TV USB port go fully
    offline when the TV is off)."""
    launched_this_boot = False
    down_since = None
    while True:
        ip = config.get("roku_ip")
        if not ip:
            time.sleep(15)
            continue
        try:
            with urllib.request.urlopen(f"http://{ip}:8060/query/device-info",
                                        timeout=3) as r:
                data = r.read()
            m = re.search(rb"<uptime>(\d+)</uptime>", data)
            up = int(m.group(1)) if m else None
            booted = (up is not None and up < 120) or \
                     (down_since is not None and time.time() - down_since > 60)
            if booted and not launched_this_boot:
                why = f"uptime={up}s" if up is not None and up < 120 else \
                      f"back online after {int(time.time() - down_since)}s"
                launched_this_boot = _roku_launch(ip, why)
            elif up is not None and up >= 300 and down_since is None:
                launched_this_boot = False
            down_since = None
        except Exception:
            if down_since is None:
                down_since = time.time()
            launched_this_boot = False
        time.sleep(15)


threading.Thread(target=_roku_watch_loop, daemon=True).start()


# ---------------------------------------------------------------- JSON API (used by Roku)

@app.get("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "name": "pi-iptv-dvr",
        "time": _now(),
        "channels": db.row(f"SELECT COUNT(*) c FROM channels {_ENABLED}")["c"],
        "channels_total": db.row("SELECT COUNT(*) c FROM channels")["c"],
        "groups_enabled": db.row("SELECT COUNT(*) c FROM groups WHERE enabled=1")["c"],
        "programs": db.row("SELECT COUNT(*) c FROM programs")["c"],
        "recordings": db.row("SELECT COUNT(*) c FROM recordings")["c"],
        "m3u_last": int(db.get_meta("m3u_last", 0) or 0),
        "epg_last": int(db.get_meta("epg_last", 0) or 0),
        "live_sessions": streamer.live.status(),
        "import": playlist.state,
    })


@app.get("/api/channels")
def api_channels():
    now = _now()
    where, args = _channel_filter()
    q = f"SELECT * FROM channels {where} ORDER BY favorite DESC, fav_order, num, name"
    with_epg = request.args.get("epg", "1") != "0"
    cur, nxt = _now_next_all(now) if with_epg else ({}, {})
    out = []
    for ch in db.rows(q, args):
        cj = _channel_json(ch)
        if with_epg:
            cj["now"] = cur.get(ch["tvg_id"])
            cj["next"] = nxt.get(ch["tvg_id"])
        out.append(cj)
    return jsonify(out)


@app.get("/api/search")
def api_search():
    """YouTube-TV-style search: matches channel names AND program titles/descriptions.
    Programs cover the current show and the next 24 h so upcoming airings can be recorded."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"channels": [], "programs": []})
    now = _now()
    where, args = _channel_filter()
    like = "%" + q + "%"

    chans = db.rows(f"SELECT * FROM channels {where} AND name LIKE ? ORDER BY favorite DESC, fav_order, num, name LIMIT 40",
                    args + [like])
    cur, nxt = _now_next_all(now)
    ch_out = []
    for ch in chans:
        cj = _channel_json(ch)
        cj["now"] = cur.get(ch["tvg_id"])
        cj["next"] = nxt.get(ch["tvg_id"])
        ch_out.append(cj)

    scheduled = {(s["channel_id"], s["start"]) for s in db.rows(
        "SELECT channel_id, start FROM schedules WHERE status IN ('scheduled','recording')")}
    progs = db.rows(
        "SELECT p.tvg_id, p.start, p.stop, p.title, p.description, "
        "c.id AS channel_id, c.name AS channel_name, c.num AS num, c.logo AS logo "
        "FROM programs p JOIN channels c ON c.tvg_id = p.tvg_id "
        "WHERE c.grp IN (SELECT name FROM groups WHERE enabled=1) "
        "AND (p.title LIKE ? OR p.description LIKE ?) AND p.stop > ? AND p.start < ? "
        "ORDER BY p.start LIMIT 60",
        (like, like, now - 3600, now + 24 * 3600))
    for p in progs:
        p["scheduled"] = (p["channel_id"], p["start"]) in scheduled
        p["now_playing"] = p["start"] <= now and p["stop"] > now
    return jsonify({"channels": ch_out, "programs": progs})


@app.get("/api/groups")
def api_groups():
    return jsonify(db.rows("SELECT name, enabled, count FROM groups ORDER BY name"))


@app.post("/api/groups")
def api_groups_save():
    """Body: {"enabled": ["Group A", "Group B"]}. Re-imports the guide for the new selection."""
    enabled = set((request.json or {}).get("enabled", []))
    c = db.conn()
    c.execute("UPDATE groups SET enabled=0")
    c.executemany("UPDATE groups SET enabled=1 WHERE name=?", [(g,) for g in enabled])
    c.commit()
    playlist.refresh_async(epg_only=True)
    return jsonify({"ok": True, "enabled": len(enabled)})


@app.post("/api/channels/<int:cid>/favorite")
def api_favorite(cid):
    fav = 1 if (request.json or {}).get("favorite", True) else 0
    if fav:
        # New favorites land at the bottom of the custom order.
        db.execute("UPDATE channels SET favorite=1, "
                   "fav_order=COALESCE((SELECT MAX(fav_order) FROM channels WHERE favorite=1),0)+1 "
                   "WHERE id=?", (cid,))
    else:
        db.execute("UPDATE channels SET favorite=0, fav_order=0 WHERE id=?", (cid,))
    return jsonify({"ok": True})


@app.post("/api/favorites/move")
def api_fav_move():
    """Rearrange favorites: {"channel_id": N, "dir": "up"|"down"|"top"|"bottom"}.
    Renumbers fav_order 1..n so ordering stays dense."""
    body = request.get_json(force=True, silent=True) or {}
    cid = int(body.get("channel_id") or 0)
    action = body.get("dir")
    ids = [r["id"] for r in db.rows(
        "SELECT id FROM channels WHERE favorite=1 ORDER BY fav_order, num, name")]
    if cid not in ids:
        abort(404)
    old = ids.index(cid)
    ids.pop(old)
    if action == "up":
        new = max(0, old - 1)
    elif action == "down":
        new = min(len(ids), old + 1)
    elif action == "top":
        new = 0
    elif action == "bottom":
        new = len(ids)
    else:
        abort(400, "dir must be up|down|top|bottom")
    ids.insert(new, cid)
    for k, fid in enumerate(ids):
        db.execute("UPDATE channels SET fav_order=? WHERE id=?", (k + 1, fid))
    return jsonify({"ok": True, "position": new})


@app.get("/api/epg/<int:cid>")
def api_epg(cid):
    ch = db.row("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
    hours = int(request.args.get("hours", 24))
    now = _now()
    progs = db.rows("SELECT * FROM programs WHERE tvg_id=? AND stop>? AND start<? ORDER BY start",
                    (ch["tvg_id"], now, now + hours * 3600))
    scheduled = {(s["channel_id"], s["start"]) for s in db.rows(
        "SELECT channel_id, start FROM schedules WHERE status IN ('scheduled','recording')")}
    for p in progs:
        p["scheduled"] = (cid, p["start"]) in scheduled
    return jsonify({"channel": _channel_json(ch), "programs": progs})


@app.get("/api/guide")
def api_guide():
    """Grid: filtered channels with programs in [from, from+hours). Accepts the
    same ?group=/?favorites=1/?q= filters as /api/channels."""
    hours = int(request.args.get("hours", 3))
    now = _now()
    start = int(request.args.get("from", now - now % 1800))
    end = start + hours * 3600
    where, args = _channel_filter()
    chans = db.rows(f"SELECT * FROM channels {where} ORDER BY favorite DESC, fav_order, num, name", args)
    ids = {c["tvg_id"] for c in chans if c["tvg_id"]}
    progs = {}
    if ids:
        for p in db.rows("SELECT tvg_id,start,stop,title,description FROM programs WHERE stop>? AND start<? ORDER BY start", (start, end)):
            if p["tvg_id"] in ids:
                progs.setdefault(p["tvg_id"], []).append(p)
    scheduled = {(s["channel_id"], s["start"]) for s in db.rows(
        "SELECT channel_id, start FROM schedules WHERE status IN ('scheduled','recording')")}
    out = []
    for ch in chans:
        cj = _channel_json(ch)
        cj["programs"] = progs.get(ch["tvg_id"], [])
        for p in cj["programs"]:
            p["scheduled"] = (ch["id"], p["start"]) in scheduled
        out.append(cj)
    return jsonify({"start": start, "end": end, "now": now, "channels": out})


@app.get("/api/schedules")
def api_schedules():
    return jsonify(db.rows(
        "SELECT s.*, c.name AS channel_name FROM schedules s LEFT JOIN channels c ON c.id=s.channel_id "
        "WHERE s.status IN ('scheduled','recording') ORDER BY s.start"))


@app.post("/api/schedules")
def api_schedule_create():
    body = request.get_json(force=True, silent=True) or request.form.to_dict()
    cid = int(body["channel_id"])
    ch = db.row("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
    now = _now()
    if body.get("program_start"):
        # record a guide entry
        prog = db.row("SELECT * FROM programs WHERE tvg_id=? AND start=?", (ch["tvg_id"], int(body["program_start"]))) or abort(404)
        start, stop, title = prog["start"], prog["stop"], prog["title"]
    elif body.get("start") and body.get("stop"):
        start, stop, title = int(body["start"]), int(body["stop"]), body.get("title") or ch["name"]
    else:
        # "record now" for N minutes (default 60) — uses current program title if known
        mins = int(body.get("minutes", 60))
        cur, _ = _now_next(ch["tvg_id"], now)
        start, stop = now, now + mins * 60
        title = body.get("title") or (cur["title"] if cur else ch["name"])
    if stop <= now:
        return jsonify({"error": "program already ended"}), 400
    dup = db.row("SELECT id FROM schedules WHERE channel_id=? AND start=? AND status IN ('scheduled','recording')", (cid, start))
    if dup:
        return jsonify({"ok": True, "id": dup["id"], "duplicate": True})
    sid = db.execute("INSERT INTO schedules(channel_id,title,start,stop,status,created) VALUES(?,?,?,?,'scheduled',?)",
                     (cid, title, start, stop, now))
    return jsonify({"ok": True, "id": sid})


@app.post("/api/timeshift")
def api_timeshift():
    """Start a live buffer recording for the current show on a channel."""
    body = request.get_json(force=True, silent=True) or request.form.to_dict()
    cid = int(body["channel_id"])
    ch = db.row("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
    now = _now()
    cur, _ = _now_next(ch["tvg_id"], now)
    if cur:
        start, stop, title = now, int(cur["stop"]), (cur.get("title") or ch["name"])
    else:
        start, stop, title = now, now + 4 * 3600, ch["name"]

    # Rejoin a live buffer that is still running for this channel (instant re-tune).
    rid = streamer.recorder.active_timeshift(cid)
    continuing = rid is not None
    if rid is None:
        # Same show still airing on this channel and its old buffer exists: restart that
        # recording in place instead of adding another duplicate row.
        old = db.row(
            "SELECT r.id FROM recordings r JOIN schedules s ON s.recording_id = r.id "
            "WHERE r.channel_id=? AND r.title=? AND s.stop=? AND r.status IN ('done','failed') "
            "ORDER BY r.id DESC LIMIT 1",
            (cid, f"[timeshift] {title}", stop))
        # Abandon the previous (un-kept) live buffer only when its provider connection
        # is needed for this tune. On a multi-connection plan it keeps rolling so
        # flipping back rejoins it with the rewind buffer intact.
        limit = streamer.recorder.max_conn or 1
        if streamer.recorder.connections_in_use() + 1 > limit:
            streamer.recorder.stop_other_timeshifts()
            # The killed streams' sockets take a moment to close on the provider's
            # side; spawning the new ffmpeg instantly can get rejected (same fix as
            # the VOD path - without it the provider kicks the new stream and the
            # reconnect shows up as a jump-back on the Roku).
            time.sleep(1)
        if streamer.recorder.connection_limit_hit():
            n = streamer.recorder.max_conn
            return jsonify({"ok": False, "error": f"Your provider only allows {n} stream(s) at once "
                            "and it is in use by a recording. Cancel the recording or upgrade "
                            "your plan for more connections."}), 503
        if len(streamer.recorder.active) >= 4:
            return jsonify({"ok": False, "error": "Too many recordings in progress"}), 503
        if old:
            rid = streamer.recorder.reuse_timeshift(old["id"], stop)
        if rid is None:
            rid = streamer.recorder.start_now(cid, start, stop, title)
    else:
        streamer.recorder.touch_timeshift(rid)
    return jsonify({
        "ok": True,
        "recording_id": rid,
        "stream_url": f"{_base_url()}/recordings/{rid}/index.m3u8",
        "title": title,
        "stop": stop,
        "continuing": continuing,
    })


@app.post("/api/timeshift/<int:rid>/keep")
def api_timeshift_keep(rid):
    """Turn a live buffer into a normal recording (runs to the end of the show, never auto-deleted)."""
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,)) or abort(404)
    new_title = rec["title"]
    if new_title.startswith("[timeshift] "):
        new_title = new_title[12:]
    db.execute("UPDATE recordings SET title=? WHERE id=?", (new_title, rid))
    db.execute("UPDATE schedules SET title=? WHERE recording_id=?", (new_title, rid))
    streamer.recorder.keep_timeshift(rid)
    return jsonify({"ok": True})


@app.post("/api/timeshift/<int:rid>/stop")
def api_timeshift_stop(rid):
    """Viewer left a live buffer without tuning elsewhere: stop it now instead of
    holding the provider connection until the idle timeout. Kept buffers refuse."""
    return jsonify({"ok": True, "stopped": streamer.recorder.stop_timeshift(rid)})


@app.post("/api/timeshift/<int:rid>/touch")
def api_timeshift_touch(rid):
    """Heartbeat from a viewer; un-touched live buffers are stopped after timeshift_idle_seconds."""
    return jsonify({"ok": True, "active": streamer.recorder.touch_timeshift(rid)})


@app.get("/api/timeshift/<int:rid>/ready")
def api_timeshift_ready(rid):
    """ready=true once enough HLS segments exist for the Roku to start without hitting the end of
    the playlist (which is what made playback stutter/loop with a single segment)."""
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,)) or abort(404)
    segs, ended, dur = streamer.recorder.segments(rid)
    streamer.recorder.touch_timeshift(rid)
    return jsonify({
        "ok": True,
        "ready": segs >= config.get("timeshift_min_segments") or (ended and segs > 0),
        "segments": segs,
        "duration": dur,
        "status": rec["status"],
        "error": streamer.recorder.last_error(rid) if rec["status"] != "recording" else "",
        "stream_url": f"{_base_url()}/recordings/{rid}/index.m3u8",
    })


@app.get("/api/vod/groups")
def api_vod_groups():
    """Movie categories with counts."""
    return jsonify(db.rows("SELECT grp AS name, COUNT(*) AS count FROM vod "
                           "GROUP BY grp ORDER BY grp"))


@app.get("/api/vod")
def api_vod():
    """Movies, optionally filtered by ?group= / ?q= (bounded for the Roku)."""
    clauses, args = [], []
    if request.args.get("group") is not None:
        clauses.append("grp=?")
        args.append(request.args["group"])
    if request.args.get("q"):
        clauses.append("name LIKE ?")
        args.append("%" + request.args["q"] + "%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return jsonify(db.rows(
        f"SELECT id, name, logo, grp FROM vod {where} ORDER BY name LIMIT 500", args))


@app.get("/api/vod/<int:vid>/info")
def api_vod_info(vid):
    """On-demand movie details (plot, rating, cast) from the Xtream provider.

    Cached in vod_info keyed by the provider's vod_id so it survives catalog
    reimports; failures are cached for an hour so scrolling a list doesn't
    hammer the provider."""
    m = db.row("SELECT * FROM vod WHERE id=?", (vid,)) or abort(404)
    cached = db.row("SELECT json, fetched FROM vod_info WHERE vod_id=?", (m["vod_id"],))
    if cached:
        try:
            info = json.loads(cached["json"] or "")
        except (ValueError, TypeError):
            info = {}
        # "mpaa" was added later - one refetch refreshes pre-existing cache entries.
        fresh_fail = not info.get("ok") and _now() - (cached["fetched"] or 0) < 3600
        if (info.get("ok") and "mpaa" in info) or fresh_fail:
            info["id"] = vid
            return jsonify(info)
    info = {"ok": False, "error": "No details available for this movie."}
    host, user, pw = (config.get(k) for k in ("xtream_host", "xtream_user", "xtream_pass"))
    if host and user and pw and m["vod_id"]:
        try:
            url = (f"{host}/player_api.php?username={user}&password={pw}"
                   f"&action=get_vod_info&vod_id={m['vod_id']}")
            with urllib.request.urlopen(url, timeout=8) as r:
                meta = (json.load(r) or {}).get("info") or {}
            info = {"ok": True,
                    "plot": meta.get("plot") or meta.get("description") or "",
                    "rating": meta.get("rating_5based") or meta.get("rating") or "",
                    "mpaa": meta.get("mpaa_rating") or "",
                    "genre": meta.get("genre") or "",
                    "cast": meta.get("cast") or "",
                    "director": meta.get("director") or "",
                    "released": meta.get("releasedate") or "",
                    "duration": meta.get("duration") or ""}
        except Exception as e:
            info = {"ok": False, "error": f"Details lookup failed: {e}"}
    db.execute("INSERT OR REPLACE INTO vod_info(vod_id, json, fetched) VALUES(?,?,?)",
               (m["vod_id"], json.dumps(info), _now()))
    info["id"] = vid
    return jsonify(info)


@app.post("/api/vod/<int:vid>/play")
def api_vod_play(vid):
    """Start muxing a movie to HLS and return its playlist URL once it has data."""
    m = db.row("SELECT * FROM vod WHERE id=?", (vid,)) or abort(404)
    try:
        pos = float(request.args.get("pos") or 0)
    except ValueError:
        pos = 0
    s = streamer.vod.sessions.get(vid)
    if s is None or not s.alive():
        # Releasing an abandoned live buffer or another movie frees the provider
        # connection; a kept/scheduled recording still wins and the user gets the
        # limit message.
        streamer.recorder.stop_other_timeshifts()
        streamer.vod.stop_others(vid)
        if streamer.recorder.connection_limit_hit():
            n = streamer.recorder.max_conn
            return jsonify({"ok": False, "error": f"Your provider only allows {n} stream(s) at once "
                            "and it is in use. Stop the recording/live TV or upgrade your "
                            "plan for more connections."}), 503
        # The killed streams' sockets take a moment to close on the provider's side;
        # spawning the movie ffmpeg instantly can get rejected.
        time.sleep(1)
    s = streamer.vod.get(m, pos)
    # Give ffmpeg a moment to fail fast on a bad URL; readiness itself is polled via
    # /api/vod/<id>/ready so this response always beats the Roku's 15 s task timeout.
    time.sleep(1.5)
    if not s.alive() and not os.path.exists(s.playlist):
        return jsonify({"ok": False, "error": "Stream did not start: " + s.last_error()}), 503
    return jsonify({"ok": True,
                    "stream_url": f"{_base_url()}/vod/{vid}/index.m3u8",
                    "title": m["name"],
                    "offset": s.start_at})


@app.post("/api/vod/<int:vid>/stop")
def api_vod_stop(vid):
    """Viewer backed out of a movie: drop the muxer and its files right away so the
    provider connection is free for whatever they pick next."""
    streamer.vod.stop(vid)
    return jsonify({"ok": True})


@app.get("/api/vod/<int:vid>/ready")
def api_vod_ready(vid):
    """Polled by the Roku while a movie is being muxed (same flow as the live buffer)."""
    s = streamer.vod.touch(vid) or abort(404)
    try:
        segs = len([f for f in os.listdir(s.dir) if f.endswith(".ts")])
    except OSError:
        segs = 0
    ended = False
    if not s.alive():
        try:
            ended = "#EXT-X-ENDLIST" in open(s.playlist).read()
        except OSError:
            pass
    return jsonify({
        "ok": True,
        "ready": segs > 0,
        "segments": segs,
        "alive": s.alive(),
        "ended": ended,
        "error": s.last_error() if not s.alive() and not ended else "",
    })


@app.post("/api/vod/<int:vid>/touch")
def api_vod_touch(vid):
    """Heartbeat while a movie is playing/paused so the muxer isn't reaped."""
    return jsonify({"ok": True, "active": streamer.vod.touch(vid) is not None})


@app.delete("/api/schedules/by-program")
def api_schedule_delete_by_program():
    """Cancel the schedule for ?channel_id=&start= (used by the guide grid)."""
    row = db.row("SELECT id FROM schedules WHERE channel_id=? AND start=? AND status IN ('scheduled','recording')",
                 (int(request.args["channel_id"]), int(request.args["start"])))
    if row:
        streamer.recorder.cancel(row["id"])
    return jsonify({"ok": True, "found": bool(row)})


@app.delete("/api/schedules/<int:sid>")
def api_schedule_delete(sid):
    streamer.recorder.cancel(sid)
    return jsonify({"ok": True})


@app.get("/api/recordings")
def api_recordings():
    out = []
    now = _now()
    for r in db.rows("SELECT * FROM recordings ORDER BY start DESC"):
        r["stream_url"] = f"{_base_url()}/recordings/{r['id']}/index.m3u8"
        # Show timeshift buffers under the show name; the prefix stays in the DB for detection.
        if r["title"].startswith("[timeshift]"):
            r["title"] = r["title"][len("[timeshift]") :].lstrip()
        if r["status"] == "recording":
            r["duration"] = now - r["start"]
        else:
            r["duration"] = r["stop"] - r["start"]
        out.append(r)
    return jsonify(out)


@app.delete("/api/recordings/<int:rid>")
def api_recording_delete(rid):
    return jsonify({"ok": streamer.delete_recording(rid)})


@app.get("/api/config")
def api_config_get():
    return jsonify(config.load())


@app.post("/api/config")
def api_config_set():
    body = request.get_json(force=True, silent=True) or request.form.to_dict()
    for k in ("refresh_hours", "pre_pad_min", "post_pad_min", "hls_segment_seconds", "hls_list_size", "live_idle_seconds"):
        if k in body:
            body[k] = int(body[k])
    for k in ("xtream_hls_input",):
        if k in body:
            body[k] = body[k] in (True, "1", "true", "on", 1)
        if k in body:
            body[k] = int(body[k])
    return jsonify(config.save(body))


@app.post("/api/refresh")
def api_refresh():
    started = playlist.refresh_async()
    return jsonify({"started": started, "import": playlist.state})


@app.post("/api/restart")
def api_restart():
    """Restart the server: answer first, then stop ffmpegs and exit - systemd's
    Restart=always brings the service back in ~5 s."""
    def _bye():
        time.sleep(1)
        streamer.shutdown()
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()
    return jsonify({"ok": True, "restarting": True})


@app.get("/api/import-status")
def api_import_status():
    return jsonify(playlist.state)


# ---------------------------------------------------------------- media

@app.get("/live/<int:cid>/<path:fname>")
def live_file(cid, fname):
    ch = db.row("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
    if fname == "index.m3u8":
        s = streamer.live.get(ch)
        if not s.wait_ready():
            abort(503, "stream did not start")
    else:
        s = streamer.live.touch(cid) or abort(404)
    resp = send_from_directory(s.dir, fname, conditional=False)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/recordings/<int:rid>/<path:fname>")
def recording_file(rid, fname):
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,)) or abort(404)
    rec_dir = os.path.join(config.get("recordings_dir"), rec["path"])
    if fname.endswith(".m3u8"):
        streamer.recorder.touch_timeshift(rid)
    resp = send_from_directory(rec_dir, fname, conditional=False)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/recordings/<int:rid>/live.m3u8")
def recording_live_window(rid):
    """Sliding-window HLS view of a timeshift buffer for browser clients.

    ffmpeg writes an EVENT playlist that grows forever — fine for Roku, but
    hls.js tracks sliding-window live playlists much more reliably. Same
    segment files, last ~3 min window, correct MEDIA-SEQUENCE."""
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,)) or abort(404)
    pl = os.path.join(config.get("recordings_dir"), rec["path"], "index.m3u8")
    try:
        lines = open(pl).read().splitlines()
    except OSError:
        abort(404)
    head, segs, ended = [], [], False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF") and i + 1 < len(lines):
            segs.append((lines[i], lines[i + 1]))
            i += 1
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif not segs:
            head.append(line)
        i += 1
    win = segs[-60:]
    seq = len(segs) - len(win)
    out = []
    for line in head:
        if line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            out.append(f"#EXT-X-MEDIA-SEQUENCE:{seq}")
        elif not line.startswith("#EXT-X-PLAYLIST-TYPE"):
            out.append(line)
    if not any(l.startswith("#EXT-X-MEDIA-SEQUENCE") for l in out):
        out.append(f"#EXT-X-MEDIA-SEQUENCE:{seq}")
    for a, b in win:
        out += [a, b]
    if ended:
        out.append("#EXT-X-ENDLIST")
    resp = app.response_class("\n".join(out) + "\n", mimetype="application/vnd.apple.mpegurl")
    resp.headers["Cache-Control"] = "no-cache"
    streamer.recorder.touch_timeshift(rid)
    return resp


@app.get("/vod/<int:vid>/<path:fname>")
def vod_file(vid, fname):
    s = streamer.vod.touch(vid) or abort(404)
    resp = send_from_directory(s.dir, fname, conditional=False)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------- tv app

_TV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv")


@app.get("/tv")
def tv_index():
    # Trailing slash so index.html's relative asset URLs (app.js, style.css)
    # resolve to /tv/... — the same relative paths keep the packaged .wgt
    # (file:// origin) working too.
    return redirect("/tv/")


@app.get("/tv/")
@app.get("/tv/<path:fname>")
def tv_file(fname="index.html"):
    resp = send_from_directory(_TV_DIR, fname, conditional=False)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/playlist.m3u")
def proxied_playlist():
    """M3U of the proxied HLS streams, handy for VLC/Kodi on the same network."""
    lines = ["#EXTM3U"]
    for ch in db.rows(f"SELECT * FROM channels {_ENABLED} ORDER BY num"):
        lines.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["grp"]}",{ch["name"]}')
        lines.append(f"{_base_url()}/live/{ch['id']}/index.m3u8")
    return "\n".join(lines) + "\n", 200, {"Content-Type": "audio/x-mpegurl"}


# ---------------------------------------------------------------- web UI

@app.get("/")
def index():
    return render_template("index.html", cfg=config.load())


@app.post("/setup")
def setup():
    updates = {"m3u_url": request.form.get("m3u_url", "").strip(),
               "epg_url": request.form.get("epg_url", "").strip()}
    if updates["m3u_url"] != config.get("m3u_url"):
        updates.update(xtream_host="", xtream_user="", xtream_pass="")
    config.save(updates)
    playlist.refresh_async()
    return redirect(url_for("index", tab="settings"))


@app.post("/setup-xtream")
def setup_xtream():
    host = request.form.get("xtream_host", "").strip().rstrip("/")
    user = request.form.get("xtream_user", "").strip()
    pw = request.form.get("xtream_pass", "").strip()
    if host and not host.startswith("http"):
        host = "http://" + host
    updates = {"xtream_host": host, "xtream_user": user, "xtream_pass": pw,
               "xtream_hls_input": request.form.get("xtream_hls_input") == "1"}
    if host and user and pw:
        creds = f"username={quote(user)}&password={quote(pw)}"
        updates["m3u_url"] = f"{host}/get.php?{creds}&type=m3u_plus&output=ts"
        updates["epg_url"] = f"{host}/xmltv.php?{creds}"
    config.save(updates)
    playlist.refresh_async()
    return redirect(url_for("index", tab="settings"))


@app.template_filter("ts")
def _fmt_ts(v):
    return time.strftime("%a %m/%d %I:%M %p", time.localtime(int(v or 0)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    if a.debug:
        app.run(host=a.host, port=a.port, debug=True, use_reloader=False, threaded=True)
    else:
        from waitress import serve
        log.info("serving on http://%s:%d", a.host, a.port)
        serve(app, host=a.host, port=a.port, threads=16)
