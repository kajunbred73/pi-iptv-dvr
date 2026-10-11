import sqlite3
import threading
import os

from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "iptv.db")
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    num INTEGER,
    name TEXT NOT NULL,
    tvg_id TEXT,
    logo TEXT,
    grp TEXT,
    url TEXT NOT NULL,
    favorite INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_channels_tvg ON channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_channels_grp ON channels(grp);

CREATE TABLE IF NOT EXISTS groups (
    name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY,
    tvg_id TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    category TEXT
);
CREATE INDEX IF NOT EXISTS idx_programs_lookup ON programs(tvg_id, start);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    title TEXT,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    status TEXT DEFAULT 'scheduled',   -- scheduled | recording | done | failed | cancelled
    recording_id INTEGER,
    created INTEGER
);

CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    channel_name TEXT,
    title TEXT,
    description TEXT,
    start INTEGER,
    stop INTEGER,
    path TEXT NOT NULL,
    status TEXT DEFAULT 'recording',   -- recording | done | failed
    size_bytes INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vod (
    id INTEGER PRIMARY KEY,
    vod_id INTEGER,
    name TEXT NOT NULL,
    logo TEXT,
    grp TEXT,
    ext TEXT,
    url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vod_grp ON vod(grp);

CREATE TABLE IF NOT EXISTS vod_info (
    vod_id INTEGER PRIMARY KEY,   -- provider's stream id, survives catalog reimports
    json TEXT,
    fetched INTEGER
);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    series_id INTEGER UNIQUE,     -- provider's series id
    name TEXT NOT NULL,
    logo TEXT,
    grp TEXT
);
CREATE INDEX IF NOT EXISTS idx_series_grp ON series(grp);

CREATE TABLE IF NOT EXISTS series_episodes (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL,   -- provider's series id (stable across catalog reimports)
    ep_id INTEGER,                -- provider's episode id; playback/resume/watched keys
    season INTEGER,
    ep INTEGER,
    title TEXT,
    url TEXT NOT NULL,
    plot TEXT,
    UNIQUE(series_id, season, ep)
);
CREATE INDEX IF NOT EXISTS idx_ser_eps ON series_episodes(series_id);

CREATE TABLE IF NOT EXISTS episode_watched (
    ep_id INTEGER PRIMARY KEY,    -- provider's episode id, stable across episode refetches
    watched_at INTEGER
);

CREATE TABLE IF NOT EXISTS series_favs (
    series_id INTEGER PRIMARY KEY,  -- provider's series id; a separate table so
                                    -- catalog reimports (DELETE FROM series) keep it
    added INTEGER,
    fav_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA)
    # Column additions for DBs created before the column existed.
    cols = [r["name"] for r in conn().execute("PRAGMA table_info(channels)")]
    if "fav_order" not in cols:
        conn().execute("ALTER TABLE channels ADD COLUMN fav_order INTEGER DEFAULT 0")
        # Existing favorites keep their current channel-number order.
        for i, r in enumerate(conn().execute(
                "SELECT id FROM channels WHERE favorite=1 ORDER BY num, name")):
            conn().execute("UPDATE channels SET fav_order=? WHERE id=?", (i + 1, r["id"]))
    ecols = [r["name"] for r in conn().execute("PRAGMA table_info(series_episodes)")]
    if "ep_id" not in ecols:
        conn().execute("ALTER TABLE series_episodes ADD COLUMN ep_id INTEGER")
        # Rows cached before ep_id existed are useless for playback keys (their
        # rowids change on every refetch) - drop them so each series refetches.
        conn().execute("DELETE FROM series_episodes")
        conn().execute("DELETE FROM meta WHERE key LIKE 'ser_eps:%'")
    # NOT in SCHEMA: it runs before this ALTER on older databases, so an index
    # on the not-yet-existing column fails and takes init() down with it.
    conn().execute("CREATE INDEX IF NOT EXISTS idx_ser_eps_epid ON series_episodes(ep_id)")
    fcols = [r["name"] for r in conn().execute("PRAGMA table_info(series_favs)")]
    if "fav_order" not in fcols:
        conn().execute("ALTER TABLE series_favs ADD COLUMN fav_order INTEGER DEFAULT 0")
        # Seed order by when each series was added so existing favorites keep it.
        for i, r in enumerate(conn().execute(
                "SELECT series_id FROM series_favs ORDER BY added, series_id")):
            conn().execute("UPDATE series_favs SET fav_order=? WHERE series_id=?",
                           (i + 1, r["series_id"]))
    conn().commit()


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def row(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def execute(sql, args=()):
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid


def set_meta(key, value):
    execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def get_meta(key, default=None):
    r = row("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else default
