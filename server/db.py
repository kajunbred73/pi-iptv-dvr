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
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA)
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
