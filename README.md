# Pi IPTV DVR

A small IPTV head-end for a Raspberry Pi plus a Roku channel to watch it on the TV.

```
 IPTV provider (M3U + XMLTV)  -->  Raspberry Pi (server/)  -->  Roku channel (roku/)
                                    - imports playlist + guide        - live TV with now/next
                                    - re-streams live TV as HLS       - guide, one-press record
                                    - DVR: schedule / record / play   - play & delete recordings
                                    - web UI for setup + scheduling
```

* **server/** – Python 3 / Flask + ffmpeg. No transcoding (remux only) so a Pi 3/4/5 keeps up.
* **roku/** – SceneGraph channel (BrightScript). Sideloaded in developer mode.

---

## 1. Raspberry Pi setup

Tested on Raspberry Pi OS (Bookworm). Needs internet access and a few GB free for recordings
(a USB drive/SSD is strongly recommended for DVR storage – see *Storage* below).

```bash
sudo apt-get install -y git
git clone https://github.com/kajunbred73/pi-iptv-dvr.git
cd pi-iptv-dvr
bash scripts/install-pi.sh          # installs python3/ffmpeg, venv, systemd service on port 8080
```

Then open `http://<pi-ip>:8080` in any browser on your network:

1. **Settings** tab → either fill in the **Xtream Codes login** (server URL, username, password) or paste
   your provider's **M3U playlist URL** and **XMLTV EPG URL** → *Save & import*. Only live channels are imported
   (movies/series are skipped; with Xtream the small `player_api` list is used instead of the giant M3U).
2. **Settings → Channel groups**: tick the groups you watch and *Save*. Only those channels are shown (web + Roku)
   and only their guide data is downloaded — essential on a Pi 3 with providers that list thousands of channels.
3. **Channels** tab lists everything with now/next from the guide. *Play* opens the HLS stream, *Rec* records the current show.
4. **Guide** tab → pick a channel → *Record* any upcoming program.
5. **Scheduled** / **Recordings** tabs manage the DVR.

The playlist and guide re-import automatically every 6 h (configurable via `POST /api/config`).

Useful commands:

```bash
journalctl -u pi-iptv-dvr -f        # logs
sudo systemctl restart pi-iptv-dvr
```

### Storage

Recordings are written to `server/data/recordings/` by default. To use a USB drive, mount it
(e.g. at `/mnt/dvr`) and point the server at it:

```bash
curl -X POST http://<pi-ip>:8080/api/config -H 'content-type: application/json' \
     -d '{"recordings_dir": "/mnt/dvr/recordings"}'
sudo systemctl restart pi-iptv-dvr
```

### Run manually (development)

```bash
cd server && python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python app.py --port 8080 --debug
```

---

## 2. Roku setup

### Enable developer mode on the Roku (one time)

1. On the Roku remote press: **Home ×3, Up ×2, Right, Left, Right, Left, Right**.
2. Accept the developer agreement and set a password (you'll use it below). The Roku reboots.
3. Note the Roku's IP (Settings → Network → About).

### Sideload the channel

**From a Mac/Linux/Pi terminal:**

```bash
ROKU_IP=192.168.1.20 ROKU_PASS=yourpassword bash scripts/roku-deploy.sh
```

**Or via the browser:** run `bash scripts/roku-deploy.sh` to produce `dist/pi-iptv-dvr.zip`, then open
`http://<roku-ip>` in a browser, log in as `rokudev` with your password, upload the zip and click *Install*.

The channel appears on the Roku home screen as **Pi IPTV DVR**. Sideloaded channels survive reboots
(only one dev channel can be installed at a time).

### First launch

The channel asks for the Pi's address – enter e.g. `192.168.1.50:8080` (it's shown on the Pi's
Settings page). Change it later under **Settings** in the channel.

Remote keys:

| Screen      | OK                | \* (options)   | Back                |
|-------------|-------------------|----------------|---------------------|
| Live TV     | watch channel     | record now     | back to menu        |
| Guide       | open channel guide → OK on a program to schedule it | | back to channel list |
| Recordings  | play              | delete         |                     |
| Scheduled   | cancel recording  |                |                     |

---

## 3. How it works

* **Live**: the first viewer of a channel starts `ffmpeg -i <provider url> -c copy -f hls` into
  `data/live/<id>/`; the Roku (or VLC, Kodi – see `/playlist.m3u`) plays `/live/<id>/index.m3u8`.
  The ffmpeg process is stopped ~30 s after the last segment request.
* **DVR**: a scheduler thread checks every 10 s; due schedules launch `ffmpeg -c copy` writing an
  *event* HLS playlist (playable while still recording) with 1 min pre-pad / 3 min post-pad.
  Finished recordings are marked VOD and listed at `/api/recordings`.
* **Guide**: XMLTV is parsed with `iterparse` (handles `.gz`, multi-hundred-MB files) and only the
  channels present in your playlist are kept, so it stays small on the Pi's SD card.

### REST API (used by the Roku channel)

| Method | Path | |
|---|---|---|
| GET | `/api/status` | counts, last import times, active live sessions |
| GET | `/api/channels[?group=]` | channels with `now`/`next` and `stream_url` |
| GET | `/api/groups` | playlist groups |
| GET | `/api/epg/<channel_id>?hours=48` | programs for one channel |
| GET | `/api/guide?hours=4` | all channels + programs (grid) |
| GET/POST | `/api/schedules` | list / create (`{channel_id, program_start}` or `{channel_id, minutes}` or `{channel_id,start,stop,title}`) |
| DELETE | `/api/schedules/<id>` | cancel |
| GET | `/api/recordings` | list with `stream_url` |
| DELETE | `/api/recordings/<id>` | delete files |
| GET/POST | `/api/config` | read / update settings |
| POST | `/api/refresh` | re-import playlist + EPG now |

---

## Troubleshooting

* **Roku says "Playback error"** – the provider stream isn't H.264/AAC (Roku can't play HEVC/MPEG-2
  audio from most models) or the Pi couldn't open the URL. Check `journalctl -u pi-iptv-dvr` and try the
  channel in VLC via `http://<pi-ip>:8080/playlist.m3u`.
* **Live channel takes ~5–10 s to start** – normal; ffmpeg waits for a keyframe to cut the first segment.
* **Provider blocks the Pi** – some providers whitelist one user-agent/IP. Change `user_agent` in
  `/api/config` and keep only one stream open per connection allowance.
* **Roku debug console** – `telnet <roku-ip> 8085` shows BrightScript errors.
