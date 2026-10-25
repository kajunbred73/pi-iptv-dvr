import json
import logging
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
    "vod_dir": os.path.join(DATA_DIR, "vod"),
    "vod_idle_seconds": 900,           # stop a movie stream nobody has touched for this long
    "refresh_hours": 6,
    "pre_pad_min": 1,
    "post_pad_min": 3,
    "user_agent": "VLC/3.0.20 LibVLC/3.0.20",
    "hls_segment_seconds": 4,
    "hls_list_size": 6,
    "live_idle_seconds": 30,
    "timeshift_idle_seconds": 120,   # stop a live buffer nobody has touched for this long
    "timeshift_keep_hours": 2,       # delete un-kept live buffers this long after they end
    "timeshift_min_segments": 3,     # segments needed before the Roku starts playing
    "xtream_hls_input": True,        # pull Xtream live streams as .m3u8 instead of raw .ts
                                     # (the provider drops raw .ts connections and resends
                                     # video on reconnect, which loops live TV on the Roku)
    "hls_input_migrated": False,     # one-time switch of old configs to xtream_hls_input on
    "audio_delay_ms": 0,             # lip-sync trim: + delays audio, - delays video (ms)
    "audio_mode": "auto",            # auto: copy AAC-LC/HE-AAC audio, re-encode the rest
                                     # copy: never re-encode | aac: always re-encode
    "roku_ip": "",                   # Roku IP(s), comma-separated - autolaunch the app when one boots
    "roku_pass": "",                 # dev-mode password - sideload updates via the dev installer
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
                try:
                    with open(CONFIG_PATH) as f:
                        _cfg.update(json.load(f))
                except (json.JSONDecodeError, OSError) as e:
                    # A hand-edited config typo must not take the whole server down;
                    # fall back to defaults and keep serving.
                    logging.getLogger("iptv").error(
                        "config.json unreadable (%s) - using defaults", e)
                else:
                    if not _cfg.get("hls_input_migrated"):
                        # Older configs saved the old raw-.ts default; move them to HLS once.
                        _cfg.update(xtream_hls_input=True, hls_input_migrated=True)
                        with open(CONFIG_PATH, "w") as f:
                            json.dump(_cfg, f, indent=2)
            os.makedirs(_cfg["recordings_dir"], exist_ok=True)
            os.makedirs(_cfg["live_dir"], exist_ok=True)
            os.makedirs(_cfg["vod_dir"], exist_ok=True)
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
