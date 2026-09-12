import json
import os
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("IPTV_DATA_DIR", os.path.join(BASE_DIR, "data"))
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    "m3u_url": "",
    "epg_url": "",
    "xtream_host": "",
    "xtream_user": "",
    "xtream_pass": "",
    "recordings_dir": os.path.join(DATA_DIR, "recordings"),
    "live_dir": os.path.join(DATA_DIR, "live"),
    "refresh_hours": 6,
    "pre_pad_min": 1,
    "post_pad_min": 3,
    "user_agent": "VLC/3.0.20 LibVLC/3.0.20",
    "hls_segment_seconds": 4,
    "hls_list_size": 6,
    "live_idle_seconds": 30,
}

_lock = threading.Lock()
_cfg = None


def load():
    global _cfg
    with _lock:
        if _cfg is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _cfg = dict(DEFAULTS)
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    _cfg.update(json.load(f))
            os.makedirs(_cfg["recordings_dir"], exist_ok=True)
            os.makedirs(_cfg["live_dir"], exist_ok=True)
        return dict(_cfg)


def save(updates):
    global _cfg
    load()
    with _lock:
        for k, v in updates.items():
            if k in DEFAULTS:
                _cfg[k] = v
        with open(CONFIG_PATH, "w") as f:
            json.dump(_cfg, f, indent=2)
        return dict(_cfg)


def get(key):
    return load()[key]
