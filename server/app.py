import argparse
import logging
import os
import threading
import time

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
    q = f"SELECT * FROM channels {where} ORDER BY favorite DESC, num, name"
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
    db.execute("UPDATE channels SET favorite=? WHERE id=?", (fav, cid))
    return jsonify({"ok": True})


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
    chans = db.rows(f"SELECT * FROM channels {where} ORDER BY favorite DESC, num, name", args)
    ids = {c["tvg_id"] for c in chans if c["tvg_id"]}
    progs = {}
    if ids:
        for p in db.rows("SELECT tvg_id,start,stop,title FROM programs WHERE stop>? AND start<? ORDER BY start", (start, end)):
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
    if rid is None:
        # Single viewer: changing channel abandons the previous (un-kept) live buffer right
        # away instead of letting several ffmpegs pile up on the Pi.
        streamer.recorder.stop_other_timeshifts()
        if len(streamer.recorder.active) >= 3:
            return jsonify({"ok": False, "error": "Too many recordings in progress"}), 503
        rid = streamer.recorder.start_now(cid, start, stop, title)
    else:
        streamer.recorder.touch_timeshift(rid)
    return jsonify({
        "ok": True,
        "recording_id": rid,
        "stream_url": f"{_base_url()}/recordings/{rid}/index.m3u8",
        "title": title,
        "stop": stop,
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


@app.post("/api/timeshift/<int:rid>/touch")
def api_timeshift_touch(rid):
    """Heartbeat from a viewer; un-touched live buffers are stopped after timeshift_idle_seconds."""
    return jsonify({"ok": True, "active": streamer.recorder.touch_timeshift(rid)})


@app.get("/api/timeshift/<int:rid>/ready")
def api_timeshift_ready(rid):
    """ready=true once enough HLS segments exist for the Roku to start without hitting the end of
    the playlist (which is what made playback stutter/loop with a single segment)."""
    rec = db.row("SELECT * FROM recordings WHERE id=?", (rid,)) or abort(404)
    segs, ended = streamer.recorder.segments(rid)
    streamer.recorder.touch_timeshift(rid)
    return jsonify({
        "ok": True,
        "ready": segs >= config.get("timeshift_min_segments") or (ended and segs > 0),
        "segments": segs,
        "status": rec["status"],
        "error": streamer.recorder.last_error(rid) if rec["status"] != "recording" else "",
        "stream_url": f"{_base_url()}/recordings/{rid}/index.m3u8",
    })


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
    return jsonify(config.save(body))


@app.post("/api/refresh")
def api_refresh():
    started = playlist.refresh_async()
    return jsonify({"started": started, "import": playlist.state})


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
    if fname.endswith(".m3u8"):
        streamer.recorder.touch_timeshift(rid)
    resp = send_from_directory(os.path.join(config.get("recordings_dir"), rec["path"]), fname, conditional=False)
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
