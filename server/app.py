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
        "channels": db.row("SELECT COUNT(*) c FROM channels")["c"],
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
    grp = request.args.get("group")
    q = "SELECT * FROM channels" + (" WHERE grp=?" if grp else "") + " ORDER BY favorite DESC, num, name"
    with_epg = request.args.get("epg", "1") != "0"
    cur, nxt = _now_next_all(now) if with_epg else ({}, {})
    out = []
    for ch in db.rows(q, (grp,) if grp else ()):
        cj = _channel_json(ch)
        if with_epg:
            cj["now"] = cur.get(ch["tvg_id"])
            cj["next"] = nxt.get(ch["tvg_id"])
        out.append(cj)
    return jsonify(out)


@app.get("/api/groups")
def api_groups():
    return jsonify([r["grp"] for r in db.rows("SELECT DISTINCT grp FROM channels WHERE grp != '' ORDER BY grp")])


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
    """Compact grid: every channel with programs for the next N hours."""
    hours = int(request.args.get("hours", 4))
    now = _now()
    end = now + hours * 3600
    progs = {}
    for p in db.rows("SELECT tvg_id,start,stop,title FROM programs WHERE stop>? AND start<? ORDER BY start", (now, end)):
        progs.setdefault(p["tvg_id"], []).append(p)
    out = []
    for ch in db.rows("SELECT * FROM channels ORDER BY favorite DESC, num, name"):
        cj = _channel_json(ch)
        cj["programs"] = progs.get(ch["tvg_id"], [])
        out.append(cj)
    return jsonify({"start": now, "end": end, "channels": out})


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


@app.delete("/api/schedules/<int:sid>")
def api_schedule_delete(sid):
    streamer.recorder.cancel(sid)
    return jsonify({"ok": True})


@app.get("/api/recordings")
def api_recordings():
    out = []
    for r in db.rows("SELECT * FROM recordings ORDER BY start DESC"):
        r["stream_url"] = f"{_base_url()}/recordings/{r['id']}/index.m3u8"
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
    resp = send_from_directory(os.path.join(config.get("recordings_dir"), rec["path"]), fname, conditional=False)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/playlist.m3u")
def proxied_playlist():
    """M3U of the proxied HLS streams, handy for VLC/Kodi on the same network."""
    lines = ["#EXTM3U"]
    for ch in db.rows("SELECT * FROM channels ORDER BY num"):
        lines.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["grp"]}",{ch["name"]}')
        lines.append(f"{_base_url()}/live/{ch['id']}/index.m3u8")
    return "\n".join(lines) + "\n", 200, {"Content-Type": "audio/x-mpegurl"}


# ---------------------------------------------------------------- web UI

@app.get("/")
def index():
    return render_template("index.html", cfg=config.load())


@app.post("/setup")
def setup():
    config.save({"m3u_url": request.form.get("m3u_url", "").strip(),
                 "epg_url": request.form.get("epg_url", "").strip()})
    playlist.refresh_async()
    return redirect(url_for("index", tab="settings"))


@app.post("/setup-xtream")
def setup_xtream():
    host = request.form.get("xtream_host", "").strip().rstrip("/")
    user = request.form.get("xtream_user", "").strip()
    pw = request.form.get("xtream_pass", "").strip()
    if host and not host.startswith("http"):
        host = "http://" + host
    updates = {"xtream_host": host, "xtream_user": user, "xtream_pass": pw}
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
