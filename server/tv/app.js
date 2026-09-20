/* Pi IPTV DVR - Samsung TV / browser client.
   Same API + flows as the Roku app: timeshift live buffers, VOD remux,
   resume positions, recents, guide overlay while playing. */

// ---------------------------------------------------------------- state

const S = {
  server: storeGet('server') || (location.protocol.startsWith('http') ? location.origin : ''),
  tabs: ['Favorites', 'Guide', 'Recent', 'Search', 'Categories', 'Movies',
         'Sports Teams', 'Recordings', 'Scheduled', 'Settings'],
  mode: 'grid',          // grid | list | categories | vodcats | vodlist | recent | teams | recordings | scheduled | settings
  items: [], contentIdx: 0,
  menuIdx: 0, menuFocus: 0,
  focus: 'menu',         // menu | content | grid | video | dialog | menubar
  gridData: null, gridRow: 0, gridCol: -1, gridFilter: '', gridFrom: 0, gridTitle: '',
  overlay: false, overlayTab: 2, overlayFocus: 'pane', barFocus: 2,
  lastQuery: '',
  // playback
  recordingId: -1, vodId: -1, vodResume: 0, vodOffset: 0, resumeIsVod: false,
  streamUrl: '', playTitle: '', isLive: false, startPos: 0,
  retryCount: 0, autoRetunes: 0, finishPos: 0, lastSegs: 0, lastErr: '',
  pendingChannel: null, playChannel: null,
  readyTimer: null, retryTimer: null, statusTimer: null, playTimer: null,
  dialogCb: null, dialogBtnIdx: 0,
  hls: null, enterDownAt: 0, enterRepeat: false,
};

const $ = id => document.getElementById(id);
const video = $('video');

function storeGet(k, d) { try { const v = localStorage.getItem('iptv.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }
function storeSet(k, v) { localStorage.setItem('iptv.' + k, JSON.stringify(v)); }
function storeDel(k) { localStorage.removeItem('iptv.' + k); }
function storeKeys() { return Object.keys(localStorage).filter(k => k.startsWith('iptv.')).map(k => k.slice(5)); }

// ---------------------------------------------------------------- helpers

function txt(v) { return v == null ? '' : String(v); }

function fmtDur(t) {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const p = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}
function fmtTime(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
function fmtDay(t) { return new Date(t * 1000).toLocaleDateString([], { weekday: 'short', month: 'numeric', day: 'numeric' }); }
function fmtSize(b) {
  if (!b) return '';
  const g = b / 1073741824;
  return g >= 0.1 ? g.toFixed(1) + ' GB' : Math.round(b / 1048576) + ' MB';
}
function urlEnc(s) { return encodeURIComponent(s); }

function api(path, cb, method = 'GET', body = null) {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), 15000);
  fetch(S.server + '/api' + path, {
    method, body, signal: ctrl.signal,
    headers: body ? { 'Content-Type': 'application/json' } : {},
  }).then(async r => {
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok && !(j && typeof j === 'object' && !Array.isArray(j))) {
      apiFail(path, `HTTP ${r.status}`); return;
    }
    if (j == null) { apiFail(path, 'bad JSON from server'); return; }
    if (Array.isArray(j)) j = { items: j };
    cb && cb(j);
  }).catch(e => {
    apiFail(path, e.name === 'AbortError' ? 'timeout contacting server' : String(e));
  }).finally(() => clearTimeout(to));
}

function apiFail(path, msg) {
  // Only the initial play requests get a real dialog; polling/heartbeats
  // (ready, touch) just retry next tick so a transient blip doesn't kill playback.
  if (path === '/timeshift' || path.endsWith('/play')) {
    stopVideo();
    showMsg("Can't play", `The Pi did not answer ${path}\n${msg}`);
  } else {
    toast('Error: ' + msg);
  }
}

function toast(msg) { $('detail').textContent = msg; }

// ---------------------------------------------------------------- navigation & rendering

function renderMenu() {
  $('menu').innerHTML = S.tabs.map((t, i) =>
    `<div class="menuitem ${i === S.menuFocus && S.focus === 'menu' ? 'sel' : ''}" data-i="${i}">${t}</div>`).join('');
}

function setRows(labels, items, emptyText = 'Nothing here yet') {
  S.items = items;
  $('content').innerHTML = labels.map((l, i) =>
    `<div class="row ${i === S.contentIdx && S.focus === 'content' ? 'sel' : ''}" data-i="${i}">${esc(l)}</div>`).join('');
  $('empty').textContent = labels.length ? '' : emptyText;
  $('grid').innerHTML = '';
  S.contentIdx = Math.min(S.contentIdx, Math.max(0, labels.length - 1));
  markSel();
}

function esc(s) { return txt(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function markSel() {
  document.querySelectorAll('#content .row').forEach((el, i) =>
    el.classList.toggle('sel', i === S.contentIdx && S.focus === 'content'));
  const sel = document.querySelector('#content .row.sel');
  if (sel) sel.scrollIntoView({ block: 'nearest' });
  updateDetail();
}

function showTab(idx) {
  S.menuIdx = idx;
  S.contentIdx = 0;
  $('heading').textContent = S.tabs[idx];
  const t = S.tabs[idx];
  if (t === 'Favorites') openGrid('favorites=1', 'Favorites');
  else if (t === 'Guide') openGrid('', 'Guide');
  else if (t === 'Recent') { S.mode = 'recent'; setHint('OK: watch   hold OK: remove'); renderRecents(); }
  else if (t === 'Search') { S.mode = 'list'; setHint('OK: search channels and shows'); S.lastQuery ? searchFor(S.lastQuery) : setRows([], [], 'Press OK to search channels and shows (e.g. ABC, ESPN, Astros).'); }
  else if (t === 'Categories') { S.mode = 'categories'; setHint('OK: open category guide'); api('/groups', onGroups); }
  else if (t === 'Movies') { S.mode = 'vodcats'; setHint('OK: open category'); api('/vod/groups', onVodGroups); }
  else if (t === 'Sports Teams') { S.mode = 'teams'; setHint('OK: team options   hold OK: add team'); renderTeams(); }
  else if (t === 'Recordings') { S.mode = 'recordings'; setHint('OK: play   hold OK: delete'); api('/recordings', onRecordings); }
  else if (t === 'Scheduled') { S.mode = 'scheduled'; setHint('OK: cancel recording'); api('/schedules', onSchedules); }
  else if (t === 'Settings') { S.mode = 'settings'; setHint(''); renderSettings(); }
}

function setHint(h) { $('hint').textContent = h; }

function openGrid(filter, title) {
  S.mode = 'grid';
  S.gridFilter = filter; S.gridTitle = title; S.gridFrom = 0;
  $('heading').textContent = title;
  setHint('Arrows: move   OK: watch / record   hold OK: favorite   Back: menu');
  $('content').innerHTML = ''; $('empty').textContent = '';
  loadGrid();
}

function loadGrid() {
  let q = '/guide?hours=3';
  if (S.gridFrom > 0) q += '&from=' + S.gridFrom;
  if (S.gridFilter) q += '&' + S.gridFilter;
  api(q, onGuide);
}

// ---------------------------------------------------------------- guide grid

const SLOT_W = 270;          // px per 30 min
const CH_W = 300;

function onGuide(r) {
  if (S.mode !== 'grid' && !S.overlay) return;
  S.gridData = r;
  S.gridRow = Math.min(S.gridRow, Math.max(0, r.channels.length - 1));
  S.gridCol = -1;
  renderGrid();
}

function cellTargets(ch, start, end) {
  // cells: channel + clipped program slots
  const cells = [{ kind: 'chan', x: 0, w: CH_W, label: '', prog: null }];
  for (const p of (ch.programs || [])) {
    const a = Math.max(p.start, start), b = Math.min(p.stop, end);
    if (b <= a) continue;
    cells.push({
      kind: 'prog', x: CH_W + (a - start) / 1800 * SLOT_W, w: (b - a) / 1800 * SLOT_W,
      label: txt(p.title), prog: p,
      now: p.start <= (S.gridData ? S.gridData.now : 0) && p.stop > (S.gridData ? S.gridData.now : 0),
    });
  }
  return cells;
}

function renderGrid() {
  const r = S.gridData;
  const el = S.overlay ? overlayEl() : $('grid');
  if (!r) { el.innerHTML = ''; return; }
  const chH = S.overlay ? 60 : 74, cw = S.overlay ? 220 : CH_W, sw = S.overlay ? 200 : SLOT_W;
  const start = r.start, end = r.end;
  let html = `<div id="timebar" style="margin-left:${cw}px">`;
  for (let t = start; t < end; t += 1800) html += `<div class="tslot" style="width:${sw}px">${fmtTime(t)}</div>`;
  html += '</div>';
  r.channels.forEach((ch, ri) => {
    html += `<div class="grow ${ri === S.gridRow ? 'sel' : ''}" style="height:${chH}px">`;
    html += `<div class="gchan ${ri === S.gridRow && S.gridCol === -1 && gridFocused() ? 'g-sel' : ''}" data-ri="${ri}" style="width:${cw}px;height:${chH}px">${ch.logo ? `<img src="${esc(ch.logo)}">` : ''}<span>${esc(txt(ch.num) + ' ' + ch.name)}</span></div>`;
    let ci = 0;
    for (const p of (ch.programs || [])) {
      const a = Math.max(p.start, start), b = Math.min(p.stop, end);
      if (b <= a) continue;
      const x = cw + (a - start) / 1800 * sw, w = (b - a) / 1800 * sw;
      const isNow = p.start <= r.now && p.stop > r.now;
      const isSel = ri === S.gridRow && S.gridCol === ci && gridFocused();
      html += `<div class="gcell ${isNow ? 'now' : ''} ${p.scheduled ? 'sched' : ''} ${isSel ? 'sel' : ''}" data-ri="${ri}" data-ci="${ci}" style="left:${x}px;width:${w}px;height:${chH - 12}px">${esc(txt(p.title))}${p.scheduled ? ' ●' : ''}</div>`;
      ci++;
    }
    html += '</div>';
  });
  el.innerHTML = html;
  const row = el.querySelectorAll('.grow')[S.gridRow];
  if (row) row.scrollIntoView({ block: 'nearest' });
  updateGridDetail();
}

// Cheap highlight-only update for arrow-key navigation — rebuilding the whole
// grid DOM per keypress is what made navigation feel flaky on the TV browser.
function updateGridSel() {
  const el = S.overlay ? overlayEl() : $('grid');
  const gf = gridFocused();
  el.querySelectorAll('.grow').forEach((r, i) => r.classList.toggle('sel', i === S.gridRow));
  el.querySelectorAll('.gchan').forEach(c =>
    c.classList.toggle('g-sel', gf && +c.dataset.ri === S.gridRow && S.gridCol === -1));
  el.querySelectorAll('.gcell').forEach(c =>
    c.classList.toggle('sel', gf && +c.dataset.ri === S.gridRow && +c.dataset.ci === S.gridCol));
  const row = el.querySelectorAll('.grow')[S.gridRow];
  if (row) row.scrollIntoView({ block: 'nearest' });
  updateGridDetail();
}
function gridFocused() { return (S.focus === 'grid' && !S.overlay) || (S.overlay && S.overlayFocus === 'pane' && ['Guide', 'Favorites'].includes(S.tabs[S.overlayTab])); }
function overlayEl() { return $('overlay-pane'); }

function updateGridDetail() {
  const r = S.gridData;
  if (!r || !r.channels.length) return;
  const ch = r.channels[S.gridRow];
  if (!ch) return;
  let info = txt(ch.name);
  const cells = (ch.programs || []).filter(pp => Math.min(pp.stop, r.end) > Math.max(pp.start, r.start));
  const p = S.gridCol >= 0 ? cells[S.gridCol] : null;
  if (p) info = txt(p.title) + '   ' + fmtDay(p.start) + ' ' + fmtTime(p.start) + ' - ' + fmtTime(p.stop) + (p.description ? '   ' + p.description.slice(0, 120) : '');
  else {
    const now = (ch.programs || []).find(pp => pp.start <= r.now && pp.stop > r.now);
    if (now) info += '   |   Now: ' + txt(now.title) + ' (' + fmtTime(now.start) + ' - ' + fmtTime(now.stop) + ')';
  }
  setDetail(info);
}

function setDetail(t) {
  if (S.overlay) $('overlay-detail').textContent = t;
  else $('detail').textContent = t;
}

// ---------------------------------------------------------------- list detail + select

function updateDetail() {
  const it = S.items[S.contentIdx];
  let d = '';
  if (it) {
    if (S.mode === 'list') {
      if (it.kind === 'program') {
        const p = it.p;
        d = txt(p.channel_name) + '   ' + fmtDay(p.start) + ' ' + fmtTime(p.start) + ' - ' + fmtTime(p.stop);
      } else {
        const ch = it.ch;
        d = txt(ch.grp);
        if (ch.now) d += '   |   Now: ' + txt(ch.now.title) + ' (' + fmtTime(ch.now.start) + ' - ' + fmtTime(ch.now.stop) + ')';
        if (ch.next) d += '   |   Next: ' + txt(ch.next.title);
      }
    } else if (S.mode === 'categories') d = txt(it.count) + ' channels';
    else if (S.mode === 'vodcats') d = txt(it.count) + ' movies';
    else if (S.mode === 'vodlist') d = txt(it.grp);
    else if (S.mode === 'recent') d = it.kind === 'movie' ? 'Movie' : txt(it.title);
    else if (S.mode === 'recordings') d = txt(it.description);
    else if (S.mode === 'teams') d = txt(it.name);
    else if (S.mode === 'scheduled') d = fmtDay(it.start) + ' ' + fmtTime(it.start) + ' - ' + fmtTime(it.stop);
  }
  setDetail(d);
}

function selectFocused() {
  const it = S.items[S.contentIdx];
  if (it == null && S.mode !== 'settings') return;
  if (S.overlay) hideOverlay();
  switch (S.mode) {
    case 'list':
      it.kind === 'program' ? programMenu(it.p) : channelMenu(it.ch);
      break;
    case 'categories':
      openGrid('group=' + urlEnc(it.name), it.name || '(no group)');
      break;
    case 'vodcats':
      S.mode = 'vodlist';
      S.vodGroup = txt(it.name);
      $('heading').textContent = 'Movies: ' + (txt(it.name) || '(no category)');
      setHint('OK: play movie   hold OK: clear resume   Back: menu');
      api('/vod?group=' + urlEnc(txt(it.name)), onVodList);
      break;
    case 'vodlist':
      selectVodItem(it);
      break;
    case 'recent':
      it.kind === 'movie' ? selectVodItem(it) : playChannel({ id: it.id, name: it.name });
      break;
    case 'teams':
      it.action === 'add' ? promptTeamAdd() : teamMenu(it.name, S.contentIdx - 1);
      break;
    case 'recordings': {
      S.recordingId = it.id;
      const isLive = it.status === 'recording';
      const saved = readResumePos(it.id);
      if (saved > 5 && !isLive) resumeDialog(it.stream_url, it.title, isLive, saved, it.id);
      else playStream(it.stream_url, it.title, isLive);
      break;
    }
    case 'scheduled':
      confirmBox(`Cancel recording '${txt(it.title)}'?`, () =>
        api('/schedules/' + it.id, onScheduleCancel, 'DELETE'));
      break;
    case 'settings': {
      const key = it;
      if (key === 'server') promptServer();
      else if (key === 'refresh') { toast('Refreshing on server...'); api('/refresh', () => toast('Refresh started'), 'POST', '{}'); }
      else if (key === 'vodclear') {
        let n = 0;
        for (const k of storeKeys()) if (k.startsWith('vpos_')) { storeDel(k); n++; }
        toast(`Cleared ${n} saved movie position(s)`);
      }
      break;
    }
  }
}

// ---------------------------------------------------------------- playback

function readResumePos(rid) { return storeGet('pos_' + rid, 0); }
function readVodPos(vid) { return storeGet('vpos_' + vid, 0); }

function playChannel(ch) {
  S.pendingChannel = ch;
  S.recordingId = -1; S.vodId = -1;
  S.playTitle = txt(ch.name);
  showLoading('Tuning...');
  api('/timeshift', r => {
    if (r.ok) {
      S.recordingId = r.recording_id;
      S.streamUrl = r.stream_url;
      S.playChannel = S.pendingChannel;
      addRecent({ kind: 'channel', id: ch.id, name: txt(ch.name), title: txt(r.title) });
      S.readyAttempts = 0;
      startReadyPoll();
    } else {
      showMsg("Can't play", 'Could not start the live buffer: ' + txt(r.error));
    }
  }, 'POST', JSON.stringify({ channel_id: ch.id }));
}

function selectVodItem(it) {
  S.vodId = it.id;
  S.playTitle = txt(it.name);
  const saved = readVodPos(it.id);
  if (saved > 5) {
    S.resumeIsVod = true;
    resumeDialog('', txt(it.name), false, saved, -1);
  } else {
    vodPlayStart(0);
  }
}

function vodPlayStart(pos) {
  S.vodResume = pos;
  showLoading('Loading movie...');
  api(`/vod/${S.vodId}/play?pos=${Math.floor(pos)}`, r => {
    if (r.ok) {
      S.streamUrl = r.stream_url;
      S.playTitle = txt(r.title);
      S.vodOffset = r.offset || 0;
      addRecent({ kind: 'movie', id: S.vodId, name: S.playTitle });
      S.readyAttempts = 0;
      startReadyPoll();
    } else {
      showMsg("Can't play", 'Could not start the movie: ' + txt(r.error));
    }
  }, 'POST', '{}');
}

function startReadyPoll() {
  clearInterval(S.readyTimer);
  S.readyTimer = setInterval(() => {
    if (S.vodId >= 0) api(`/vod/${S.vodId}/ready`, onVodReady);
    else if (S.recordingId >= 0) api(`/timeshift/${S.recordingId}/ready`, onReady);
  }, 1000);
}

function onReady(r) {
  if (r.ready) {
    clearInterval(S.readyTimer);
    const startPos = r.duration > 30 ? r.duration - 15 : r.duration > 20 ? r.duration - 10 : 0;
    playStream(S.streamUrl, S.playTitle, true, startPos);
  } else if (r.status !== 'recording') {
    clearInterval(S.readyTimer);
    showMsg("Can't play", "The Pi could not open this channel's stream. ffmpeg said:\n" + txt(r.error));
  } else {
    S.readyAttempts = (S.readyAttempts || 0) + 1;
    setLoadingProgress(`Buffering live TV... ${r.segments}/3`);
    if (S.readyAttempts > 90) {
      clearInterval(S.readyTimer);
      showMsg("Can't play", `The Pi is still not producing video after 90 s (${r.segments} segments).`);
    }
  }
}

function onVodReady(r) {
  if (r.ready) {
    clearInterval(S.readyTimer);
    const startPos = Math.max(0, S.vodResume - S.vodOffset);
    playStream(S.streamUrl, S.playTitle, false, startPos);
  } else if (!r.alive) {
    clearInterval(S.readyTimer);
    showMsg("Can't play", txt(r.error)
      ? 'The Pi could not start this movie. ffmpeg said:\n' + txt(r.error)
      : 'The movie stream stopped before it produced any video.');
  } else {
    S.readyAttempts = (S.readyAttempts || 0) + 1;
    setLoadingProgress(`Loading movie... ${r.segments} segments`);
    if (S.readyAttempts > 240) {
      clearInterval(S.readyTimer);
      showMsg("Can't play", 'The movie still is not ready after 4 minutes.');
    }
  }
}

function setStream(url) {
  if (S.hls) { S.hls.destroy(); S.hls = null; }
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
  } else if (window.Hls && Hls.isSupported()) {
    // Tuned for our EVENT playlists: deeper buffer absorbs provider stalls,
    // maxBufferHole jumps over timestamp discontinuities the provider's TS
    // stream produces (Roku tolerates them, hls.js stalls on them).
    S.hls = new Hls({
      liveDurationInfinity: true,
      liveSyncDurationCount: 4,
      maxLiveSyncPlaybackRate: 1.5,
      maxBufferLength: 60,
      backBufferLength: 30,
      maxBufferHole: 2,
      nudgeMaxRetry: 10,
      fragLoadingMaxRetry: 6,
      manifestLoadingMaxRetry: 6,
      levelLoadingMaxRetry: 6,
    });
    S.hls.loadSource(url);
    S.hls.attachMedia(video);
    S.hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    S.hls.on(Hls.Events.ERROR, (e, data) => {
      if (!data || !data.fatal) return;
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) { try { S.hls.recoverMediaError(); } catch (x) {} }
      else if (S.isLive && S.recordingId >= 0) rejoinCheck();
      else showMsg("Can't play", 'Stream error while playing.');
    });
  } else {
    loadHlsJs(() => setStream(url));
  }
}

let hlsLoading = false;
function loadHlsJs(done) {
  if (hlsLoading) return;
  hlsLoading = true;
  const s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js';
  s.onload = done;
  s.onerror = () => showMsg("Can't play", 'This browser cannot play HLS and hls.js failed to load.');
  document.head.appendChild(s);
}

function playStream(url, title, isLive, startPos = 0) {
  S.streamUrl = url; S.playTitle = title; S.isLive = isLive; S.startPos = startPos;
  S.retryCount = 0; S.finishPos = 0;
  setStream(url);
  video.classList.add('playing');
  video.classList.remove('pip');
  video.play().catch(() => {});
  S.focus = 'video';
  renderMenu(); markSel();
  $('osd').classList.add('show');
  $('osd-title').textContent = title;
  $('osd-hint').textContent = 'Click/OK: controls   Up: menu   Back: stop   FF/RW: ±30s';
  clearTimeout(S.playTimer);
  S.playTimer = setTimeout(() => {
    if (video.classList.contains('playing') && video.paused && !video.currentTime)
      showMsg("Can't play", 'The stream did not start within 30 seconds. Check the Pi service.');
  }, 30000);
}

video.addEventListener('playing', () => {
  hideLoading();
  clearTimeout(S.playTimer);
  if (S.startPos > 0) { video.currentTime = S.startPos; S.startPos = 0; }
  S.retryCount = 0; S.autoRetunes = 0;
});
video.addEventListener('error', () => {
  if (!video.classList.contains('playing')) return;
  if (S.isLive && S.recordingId >= 0 && S.retryCount < 5) {
    S.retryCount++;
    setTimeout(rejoinCheck, 3000);
  } else {
    showMsg("Can't play", 'Playback error (' + (video.error ? video.error.code : '?') + ')');
  }
});
video.addEventListener('ended', () => {
  if (S.isLive && S.recordingId >= 0) {
    S.finishPos = video.currentTime || S.finishPos;
    rejoinCheck();
  } else {
    stopVideo(true);
  }
});

// Stall watchdog: a live buffer freeze at the playlist edge fires no 'error'
// or 'ended' — playback just hangs. Detect "position frozen + no buffered
// runway" and rejoin, which is what 'Jump to live' did manually.
let lastPos = -1, stallTicks = 0;
setInterval(() => {
  if (!video.classList.contains('playing') || video.paused || S.focus === 'dialog') {
    lastPos = -1; stallTicks = 0; return;
  }
  const pos = video.currentTime;
  const buffered = video.buffered.length
    ? video.buffered.end(video.buffered.length - 1) - pos : 0;
  if (pos === lastPos && buffered < 1) {
    if (++stallTicks >= 3) {
      stallTicks = 0;
      if (S.isLive && S.recordingId >= 0) rejoinCheck();
      else if (S.hls) { try { S.hls.startLoad(); } catch (e) {} }
      else video.currentTime = pos + 0.5;
    }
  } else {
    stallTicks = 0;
  }
  lastPos = pos;
}, 2000);

function rejoinCheck() {
  api(`/timeshift/${S.recordingId}/ready`, r => {
    if (!video.classList.contains('playing')) return;
    S.lastErr = txt(r.error); S.lastSegs = r.segments;
    if (r.status === 'recording') {
      if (r.duration > S.finishPos + 6) rejoinLive();
      else setTimeout(rejoinCheck, 3000);
    } else if (r.status === 'done') {
      if (S.autoRetunes < 3 && S.playChannel) {
        S.autoRetunes++;
        S.pendingChannel = S.playChannel;
        api('/timeshift', r2 => {
          if (r2.ok) { S.recordingId = r2.recording_id; S.streamUrl = r2.stream_url; startReadyPoll(); }
          else stopVideo(true);
        }, 'POST', JSON.stringify({ channel_id: S.playChannel.id }));
      } else stopVideo(true);
    } else {
      showMsg("Can't play", `The Pi stopped this channel's recording (${txt(r.status)}). ${txt(r.error)}`);
    }
  });
}

function rejoinLive() {
  const dur = video.duration;
  setStream(S.streamUrl);
  video.play().catch(() => {});
  S.startPos = (isFinite(dur) && dur > 20) ? dur - 15 : Math.max(0, S.finishPos - 3);
}

function stopVideo(clearResume = false) {
  // save/clear resume
  let key = '';
  if (S.vodId >= 0) key = 'vpos_' + S.vodId;
  else if (S.recordingId >= 0) key = 'pos_' + S.recordingId;
  if (key) {
    if (clearResume) storeDel(key);
    else {
      const pos = video.currentTime || 0, dur = video.duration || 0;
      if (pos > 5 && (!isFinite(dur) || pos < dur - 15)) storeSet(key, pos);
    }
  }
  if (S.vodId >= 0) api(`/vod/${S.vodId}/stop`, null, 'POST', '{}');
  clearInterval(S.readyTimer); clearTimeout(S.playTimer); clearTimeout(S.retryTimer);
  video.pause();
  video.removeAttribute('src');
  if (S.hls) { S.hls.destroy(); S.hls = null; }
  video.classList.remove('playing', 'pip');
  $('osd').classList.remove('show');
  hideOverlay();
  hideLoading();
  S.recordingId = -1; S.vodId = -1; S.isLive = false; S.retryCount = 0;
  S.focus = S.mode === 'grid' ? 'grid' : 'content';
  renderMenu(); markSel();
}

// ---------------------------------------------------------------- trick menu

function trickMenu() {
  if (S.recordingId < 0 && S.vodId < 0) return;
  const pos = video.currentTime || 0, dur = video.duration || 0;
  let info = 'Position ' + fmtDur(pos);
  if (isFinite(dur) && dur > 0) info += ' / ' + fmtDur(dur);
  if (S.isLive) info += '   (live buffer)';
  const btns = video.paused
    ? ['Play', 'Back 30s', 'Forward 30s']
    : ['Pause', 'Back 30s', 'Forward 30s'];
  if (S.isLive) { btns.push('Jump to live', 'Keep recording'); }
  btns.push('Channel menu', 'Stop watching');
  showMsg(S.playTitle, info, btns, idx => {
    const a = btns[idx];
    if (a === 'Pause') video.pause();
    else if (a === 'Play') video.play().catch(() => {});
    else if (a === 'Back 30s') video.currentTime = Math.max(0, video.currentTime - 30);
    else if (a === 'Forward 30s') video.currentTime = video.currentTime + 30;
    else if (a === 'Jump to live') rejoinLive();
    else if (a === 'Keep recording') api(`/timeshift/${S.recordingId}/keep`, () => toast('Recording saved'), 'POST', '');
    else if (a === 'Channel menu') { S.focus = 'video'; showOverlay(); }
    else if (a === 'Stop watching') stopVideo();
  });
}

// ---------------------------------------------------------------- recents

function loadRecents() { return storeGet('recent', []); }
function addRecent(item) {
  let recents = loadRecents().filter(r => !(r.id === item.id && r.kind === item.kind));
  recents.unshift(item);
  recents = recents.slice(0, 15);
  storeSet('recent', recents);
}
function recentLabel(r) {
  if (r.kind === 'movie') return 'MOVIE   ' + txt(r.name);
  return txt(r.name) + (txt(r.title) ? '   -   ' + txt(r.title) : '');
}
function renderRecents() {
  const items = loadRecents();
  setRows(items.map(recentLabel), items,
    'Nothing watched yet. Tune a channel or play a movie and it shows up here.');
}
function removeRecentFocused() {
  const items = loadRecents();
  items.splice(S.contentIdx, 1);
  storeSet('recent', items);
  renderRecents();
  toast('Removed');
}

// ---------------------------------------------------------------- api response renderers

function onGroups(r) {
  if (S.mode !== 'categories' && !S.overlay) return;
  const items = (r.items || []).filter(g => g.enabled === 1);
  setRows(items.map(g => (txt(g.name) || '(no group)') + '   (' + txt(g.count) + ')'), items,
    'No groups enabled. On the Pi web page go to Settings > Channel groups.');
}
function onVodGroups(r) {
  if (S.mode !== 'vodcats' && !S.overlay) return;
  const items = r.items || [];
  setRows(items.map(g => (txt(g.name) || '(no category)') + '   (' + txt(g.count) + ')'), items,
    'No movies found. Try Refresh playlist and guide on server from Settings.');
}
function onVodList(r) {
  if (S.mode !== 'vodlist') return;
  const items = r.items || [];
  setRows(items.map(mv => txt(mv.name) + (readVodPos(mv.id) > 5 ? '   (resume ' + fmtDur(readVodPos(mv.id)) + ')' : '')),
    items, 'No movies in this category.');
}
function onRecordings(r) {
  if (S.mode !== 'recordings') return;
  const items = r.items || [];
  setRows(items.map(rec => {
    let tag = rec.status === 'recording' ? '  [RECORDING]' : rec.status === 'failed' ? '  [FAILED]' : '';
    const saved = readResumePos(rec.id);
    if (rec.status !== 'recording' && saved > 5 && rec.duration > 0)
      tag += '   ' + Math.min(100, Math.floor(saved / rec.duration * 100)) + '% watched';
    return fmtDay(rec.start) + ' ' + fmtTime(rec.start) + '   ' + txt(rec.title) +
      '   (' + fmtDur(rec.duration) + ', ' + txt(rec.channel_name) + ', ' + fmtSize(rec.size_bytes) + ')' + tag;
  }), items, 'No recordings yet. Pick a show in the Guide and choose Record.');
}
function onSchedules(r) {
  if (S.mode !== 'scheduled') return;
  const items = r.items || [];
  setRows(items.map(s => fmtDay(s.start) + ' ' + fmtTime(s.start) + '   ' + txt(s.title) +
    '   (' + txt(s.channel_name) + ')' + (s.status === 'recording' ? '  [RECORDING]' : '')),
    items, 'Nothing scheduled.');
}
function onScheduleCancel() { toast('Recording cancelled'); if (S.mode === 'scheduled') api('/schedules', onSchedules); }

function searchFor(q) {
  S.lastQuery = q;
  $('heading').textContent = 'Search: ' + q;
  api('/search?q=' + urlEnc(q), r => {
    if (S.mode !== 'list') return;
    const items = [];
    const labels = [];
    for (const ch of (r.channels || [])) {
      labels.push((ch.favorite ? '*   ' : '    ') + txt(ch.num) + '   ' + txt(ch.name) +
        (ch.now ? '   -   ' + txt(ch.now.title) : ''));
      items.push({ kind: 'channel', ch });
    }
    for (const p of (r.programs || [])) {
      labels.push('TV   ' + txt(p.title) + '   ' + fmtDay(p.start) + ' ' + fmtTime(p.start) +
        '   (' + txt(p.channel_name) + ')' + (p.now_playing ? '   [ON NOW]' : '') + (p.scheduled ? '   [REC]' : ''));
      items.push({ kind: 'program', p });
    }
    setRows(labels, items, "Nothing matches '" + q + "'.");
    setHint('OK: watch / record / favorite   hold OK: star   Back: menu');
  });
}

// ---------------------------------------------------------------- menus & dialogs

function channelMenu(ch) {
  const now = ch.now;
  const btns = ['Watch', 'Record current show', (ch.favorite ? 'Remove favorite' : 'Add favorite'), 'Close'];
  showMsg(txt(ch.num) + ' ' + txt(ch.name), now ? 'Now: ' + txt(now.title) : '', btns, idx => {
    if (idx === 0) playChannel(ch);
    else if (idx === 1) api('/schedules', () => toast('Recording scheduled'), 'POST',
      JSON.stringify({ channel_id: ch.id, program_start: now ? now.start : undefined }));
    else if (idx === 2) api(`/channels/${ch.id}/favorite`, () => {}, 'POST', JSON.stringify({ favorite: !ch.favorite }));
  });
}

function programMenu(p) {
  const btns = [p.now_playing ? 'Watch now' : 'Watch channel now',
    p.scheduled ? 'Cancel recording' : 'Record', 'Close'];
  showMsg(txt(p.title), txt(p.channel_name) + '   ' + fmtDay(p.start) + ' ' + fmtTime(p.start) + ' - ' + fmtTime(p.stop), btns, idx => {
    if (idx === 0) playChannel({ id: p.channel_id, name: p.channel_name });
    else if (idx === 1) {
      if (p.scheduled) api(`/schedules/by-program?channel_id=${p.channel_id}&start=${p.start}`, onScheduleCancel, 'DELETE');
      else api('/schedules', () => toast('Recording scheduled'), 'POST', JSON.stringify({ channel_id: p.channel_id, program_start: p.start }));
    }
  });
}

// generic button dialog
function showMsg(title, msg, buttons = ['OK'], cb = null) {
  hideLoading();
  S.dialogCb = cb;
  S.dialogInputCb = null;
  S.dialogBtnIdx = 0;
  $('dialog-title').textContent = title;
  $('dialog-msg').textContent = msg;
  $('dialog-progress').textContent = '';
  $('dialog-input').classList.add('hidden');
  renderDialogButtons(buttons);
  $('dialog').classList.remove('hidden');
  S.focus = 'dialog';
}
function renderDialogButtons(buttons) {
  S.dialogButtons = buttons;
  $('dialog-buttons').innerHTML = buttons.map((b, i) =>
    `<div class="dbtn ${i === S.dialogBtnIdx ? 'sel' : ''}" data-i="${i}">${esc(b)}</div>`).join('');
}

// Shared by Enter key and mouse/TV-cursor click.
function dialogChoose(idx) {
  const inp = $('dialog-input');
  if (!inp.classList.contains('hidden') && idx === 0) {
    const cb = S.dialogInputCb, v = inp.value.trim();
    closeDialog(); cb && cb(v);
    return;
  }
  const cb = S.dialogCb;
  closeDialog(); cb && cb(idx);
}
function closeDialog() {
  $('dialog').classList.add('hidden');
  $('dialog-input').blur();
  S.dialogCb = null;
  S.resumeIsVod = false;
  S.focus = video.classList.contains('playing') ? 'video' : 'content';
  renderMenu(); markSel();
}

function confirmBox(msg, cb) { showMsg('Are you sure?', msg, ['Yes', 'No'], i => { if (i === 0) cb(); }); }

function inputDialog(title, initial, cb) {
  S.dialogCb = null;
  S.dialogInputCb = cb;
  $('dialog-title').textContent = title;
  $('dialog-msg').textContent = '';
  $('dialog-progress').textContent = '';
  const inp = $('dialog-input');
  inp.classList.remove('hidden');
  inp.value = initial || '';
  renderDialogButtons(['OK', 'Cancel']);
  S.dialogBtnIdx = 0;
  $('dialog').classList.remove('hidden');
  S.focus = 'dialog';
  inp.focus();
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.stopPropagation(); closeDialog(); cb(inp.value.trim()); }
  };
}

function showLoading(title) {
  S.dialogCb = null;
  $('dialog-title').textContent = title;
  $('dialog-msg').textContent = S.playTitle || '';
  $('dialog-progress').textContent = '';
  $('dialog-input').classList.add('hidden');
  $('dialog-buttons').innerHTML = '';
  $('dialog').classList.remove('hidden');
  S.focus = 'dialog';
}
function setLoadingProgress(t) { $('dialog-progress').textContent = t; }
function hideLoading() {
  if (S.focus === 'dialog' && !$('dialog-buttons').children.length) closeDialog();
  else if (!$('dialog-buttons').children.length) $('dialog').classList.add('hidden');
}

function resumeDialog(url, title, isLive, saved, rid) {
  showMsg('Resume viewing?', 'Resume from ' + fmtDur(saved) + ' or start over?', ['Resume', 'Start over'], idx => {
    if (S.resumeIsVod) { S.resumeIsVod = false; vodPlayStart(idx === 0 ? saved : 0); return; }
    S.recordingId = rid;
    playStream(url, title, isLive, idx === 0 ? saved : 0);
  });
}

// ---------------------------------------------------------------- teams

function loadTeams() { return storeGet('teams', []); }
function saveTeams(t) { storeSet('teams', t); }
function renderTeams() {
  const teams = loadTeams();
  const items = [{ name: '+ Add a team', action: 'add' }].concat(teams.map(n => ({ name: n })));
  setRows(items.map(i => i.name), items, 'No teams saved.');
}
function promptTeamAdd() {
  inputDialog('Add sports team', '', name => {
    if (!name) return;
    const teams = loadTeams(); teams.push(name); saveTeams(teams); renderTeams();
    findGame(name);
  });
}
function teamMenu(name, idx) {
  showMsg(name, '', ['Find game', 'Rename', 'Delete', 'Cancel'], i => {
    const teams = loadTeams();
    if (i === 0) findGame(name);
    else if (i === 1) inputDialog('Rename sports team', name, nn => {
      if (nn) { teams[idx] = nn; saveTeams(teams); renderTeams(); }
    });
    else if (i === 2) { teams.splice(idx, 1); saveTeams(teams); renderTeams(); }
  });
}
function findGame(name) {
  showLoading('Searching for ' + name + '...');
  api('/channels?q=' + urlEnc(name), r => {
    hideLoading();
    const items = r.items || r.channels || [];
    if (!items.length) { toast('No live game found for ' + name); return; }
    if (items.length === 1) { playChannel(items[0]); return; }
    showMsg('Select channel playing ' + name, '', items.map(c => txt(c.num) + ' ' + txt(c.name) + (c.now && c.now.title ? ' - ' + c.now.title : '')),
      i => playChannel(items[i]));
  });
}

// ---------------------------------------------------------------- settings

function renderSettings() {
  const rows = [];
  const keys = [];
  if (!location.protocol.startsWith('http')) { rows.push('Server address: ' + (S.server || '(not set)')); keys.push('server'); }
  rows.push('Refresh playlist and guide on server', 'Clear all movie resume marks', 'Version 1.1 (TV)');
  keys.push('refresh', 'vodclear', 'version');
  setRows(rows, keys);
}
function promptServer() {
  inputDialog('Pi address (e.g. http://192.168.1.50:8080)', S.server, v => {
    if (!v) return;
    S.server = v.replace(/\/+$/, '');
    storeSet('server', S.server);
    loadStatus();
    S.mode === 'settings' ? renderSettings() : showTab(0);
  });
}

// ---------------------------------------------------------------- overlay (PiP menu while playing)

function buildMenuBar() {
  $('menubar').innerHTML = S.tabs.map((t, i) =>
    `<div class="baritem ${i === S.overlayTab ? 'sel' : ''} ${i === S.barFocus && S.overlayFocus === 'menubar' ? 'focus' : ''}" data-i="${i}">${t}</div>`).join('');
}

function showOverlay() {
  S.overlay = true;
  S.overlayFocus = 'pane';
  S.barFocus = S.overlayTab;
  video.classList.add('pip');
  $('overlay').classList.add('show');
  applyOverlayTab(S.overlayTab);
  $('osd').classList.remove('show');
}

function hideOverlay() {
  S.overlay = false;
  $('overlay').classList.remove('show');
  video.classList.remove('pip');
}

function applyOverlayTab(idx) {
  S.overlayTab = idx;
  S.overlayFocus = 'pane';
  ovSelect = null;
  buildMenuBar();
  const t = S.tabs[idx];
  if (t === 'Guide' || t === 'Favorites') {
    S.gridFilter = t === 'Favorites' ? 'favorites=1' : '';
    S.gridFrom = 0;
    loadGrid();   // onGuide renders into overlay-pane while S.overlay
  } else if (t === 'Recent') {
    const items = loadRecents();
    overlayList(items.map(recentLabel), items, i => {
      hideOverlay();
      const r = items[i];
      r.kind === 'movie' ? selectVodItem(r) : playChannel({ id: r.id, name: r.name });
    });
  } else if (t === 'Movies') {
    api('/vod/groups', r => {
      if (!S.overlay) return;
      const cats = r.items || [];
      overlayList(cats.map(g => (txt(g.name) || '(no category)') + '   (' + g.count + ')'), cats, i => {
        api('/vod?group=' + urlEnc(txt(cats[i].name)), r2 => {
          if (!S.overlay) return;
          const mvs = r2.items || [];
          overlayList(mvs.map(mv => txt(mv.name)), mvs, j => { hideOverlay(); selectVodItem(mvs[j]); });
        });
      });
    });
  } else if (t === 'Recordings') {
    api('/recordings', r => {
      if (!S.overlay) return;
      const its = r.items || [];
      overlayList(its.map(x => fmtDay(x.start) + ' ' + fmtTime(x.start) + '   ' + txt(x.title)), its, i => {
        hideOverlay();
        const rec = its[i];
        S.recordingId = rec.id;
        const s = readResumePos(rec.id);
        s > 5 && rec.status !== 'recording'
          ? resumeDialog(rec.stream_url, rec.title, false, s, rec.id)
          : playStream(rec.stream_url, rec.title, rec.status === 'recording');
      });
    });
  } else if (t === 'Categories') {
    api('/groups', r => {
      if (!S.overlay) return;
      const its = (r.items || []).filter(g => g.enabled);
      overlayList(its.map(g => (txt(g.name) || '(no group)') + '   (' + g.count + ')'), its, i => {
        hideOverlay();
        openGrid('group=' + urlEnc(its[i].name), its[i].name || '(no group)');
        S.focus = 'grid';
      });
    });
  } else if (t === 'Sports Teams') {
    const teams = loadTeams();
    const items = [{ name: '+ Add a team', action: 'add' }].concat(teams.map(n => ({ name: n })));
    overlayList(items.map(i => i.name), items, i => {
      hideOverlay();
      const it = items[i];
      it.action === 'add' ? promptTeamAdd() : findGame(it.name);
    });
  } else if (t === 'Scheduled') {
    api('/schedules', r => {
      if (!S.overlay) return;
      const its = r.items || [];
      overlayList(its.map(s => fmtDay(s.start) + ' ' + fmtTime(s.start) + '   ' + txt(s.title)), its, () => {});
    });
  } else if (t === 'Search') {
    hideOverlay();
    inputDialog('Search channels and shows', '', q => { if (q) { S.mode = 'list'; searchFor(q); S.focus = 'content'; } });
  } else if (t === 'Settings') {
    hideOverlay();
    showTab(S.tabs.indexOf('Settings'));
    S.focus = 'content';
  }
}

// Generic overlay list with its own focus index.
let ovItems = [], ovIdx = 0, ovSelect = null;
function overlayList(labels, items, onSel = null) {
  ovItems = items || labels.map((l, i) => i);
  ovIdx = 0; ovSelect = onSel;
  renderOverlayList(labels);
}
function renderOverlayList(labels) {
  $('overlay-pane').innerHTML = labels.map((l, i) =>
    `<div class="row ${i === ovIdx ? 'sel' : ''}" data-i="${i}">${esc(l)}</div>`).join('');
  const sel = $('overlay-pane').querySelector('.row.sel');
  if (sel) sel.scrollIntoView({ block: 'nearest' });
}
function overlayMove(d) {
  ovIdx = Math.max(0, Math.min(ovIdx + d, ovItems.length - 1));
  document.querySelectorAll('#overlay-pane .row').forEach((el, i) => el.classList.toggle('sel', i === ovIdx));
  const sel = $('overlay-pane .row.sel');
  if (sel) sel.scrollIntoView({ block: 'nearest' });
}

// ---------------------------------------------------------------- grid select/focus actions

function gridSelect() {
  const r = S.gridData;
  if (!r || !r.channels.length) return;
  const ch = r.channels[S.gridRow];
  if (S.gridCol < 0) { channelMenu(ch); return; }
  const cells = (ch.programs || []).filter(pp => Math.min(pp.stop, r.end) > Math.max(pp.start, r.start));
  const p = cells[S.gridCol];
  if (p) { p.channel_id = ch.id; p.channel_name = ch.name; p.now_playing = p.start <= r.now && p.stop > r.now; programMenu(p); }
}

function gridMoveCol(d) {
  const ch = S.gridData.channels[S.gridRow];
  const n = (ch.programs || []).filter(pp => Math.min(pp.stop, S.gridData.end) > Math.max(pp.start, S.gridData.start)).length;
  S.gridCol = Math.max(-1, Math.min(S.gridCol + d, n - 1));
  updateGridSel();
}

// ---------------------------------------------------------------- options (long-OK / red)

function optionsAction() {
  if (S.focus === 'grid' && S.gridData && S.gridData.channels.length) {
    const ch = S.gridData.channels[S.gridRow];
    api(`/channels/${ch.id}/favorite`, () => loadGrid(), 'POST', JSON.stringify({ favorite: !ch.favorite }));
    return;
  }
  if (S.focus !== 'content') return;
  const it = S.items[S.contentIdx];
  if (it == null) return;
  if (S.mode === 'list' && it.kind === 'channel') {
    api(`/channels/${it.ch.id}/favorite`, () => { toast(it.ch.favorite ? 'Removed from favorites' : 'Added to favorites'); searchFor(S.lastQuery); }, 'POST', JSON.stringify({ favorite: !it.ch.favorite }));
  } else if (S.mode === 'recordings') {
    confirmBox(`Delete '${txt(it.title)}'?`, () => api('/recordings/' + it.id, () => { toast('Deleted'); api('/recordings', onRecordings); }, 'DELETE'));
  } else if (S.mode === 'vodlist') {
    storeDel('vpos_' + it.id);
    api('/vod?group=' + urlEnc(S.vodGroup || ''), onVodList);
    toast('Resume point cleared');
  } else if (S.mode === 'recent') {
    removeRecentFocused();
  } else if (S.mode === 'teams' && S.contentIdx > 0) {
    const teams = loadTeams(); teams.splice(S.contentIdx - 1, 1); saveTeams(teams); renderTeams();
  }
}

// ---------------------------------------------------------------- status/heartbeat

function loadStatus() {
  if (!S.server) return;
  api('/status', r => {
    $('status').textContent = `${r.channels} channels  |  ${r.recordings} recordings  |  ${S.server}`;
  }, 'GET', null);
  if (video.classList.contains('playing')) {
    if (S.isLive && S.recordingId >= 0) api(`/timeshift/${S.recordingId}/touch`, null, 'POST', '');
    else if (S.vodId >= 0) api(`/vod/${S.vodId}/touch`, null, 'POST', '');
  }
}

// ---------------------------------------------------------------- keys

const TIZEN_BACK = 10009, RED = 403, FF = 417, RW = 412, PLAY = 415, PAUSE = 19, PLAYPAUSE = 10252;

document.addEventListener('keydown', e => {
  const k = e.key, c = e.keyCode;

  // dialog mode
  if (S.focus === 'dialog') {
    if (e.target === $('dialog-input') && !$('dialog-input').classList.contains('hidden')) {
      if (k === 'Backspace' || c === TIZEN_BACK || k === 'Escape') { e.stopPropagation(); closeDialog(); }
      else if (k === 'ArrowDown') e.target.blur();   // drop to the OK/Cancel buttons
      return;
    }
    if (k === 'ArrowLeft') { S.dialogBtnIdx = Math.max(0, S.dialogBtnIdx - 1); renderDialogButtons(S.dialogButtons); }
    else if (k === 'ArrowRight') { S.dialogBtnIdx = Math.min(S.dialogButtons.length - 1, S.dialogBtnIdx + 1); renderDialogButtons(S.dialogButtons); }
    else if (k === 'Enter') dialogChoose(S.dialogBtnIdx);
    else if (k === 'Backspace' || c === TIZEN_BACK || k === 'Escape') {
      closeDialog();
      if (S.dialogInputCb) { S.dialogInputCb = null; }
    }
    e.preventDefault();
    return;
  }

  // playback mode
  if (S.focus === 'video') {
    const back = c === TIZEN_BACK || k === 'Backspace' || k === 'Escape';
    if (S.overlay) {
      const gridTab = ['Guide', 'Favorites'].includes(S.tabs[S.overlayTab]);
      if (back) hideOverlay();
      else if (S.overlayFocus === 'menubar') {
        if (k === 'ArrowLeft') { S.barFocus = Math.max(0, S.barFocus - 1); buildMenuBar(); }
        else if (k === 'ArrowRight') { S.barFocus = Math.min(S.tabs.length - 1, S.barFocus + 1); buildMenuBar(); }
        else if (k === 'ArrowDown' || k === 'Enter') { applyOverlayTab(S.barFocus); }
        else if (k === 'ArrowUp') { /* already on menubar */ }
      } else if (gridTab && S.gridData) {
        if (k === 'ArrowUp') { S.gridRow = Math.max(0, S.gridRow - 1); S.gridCol = -1; updateGridSel(); }
        else if (k === 'ArrowDown') { S.gridRow = Math.min(S.gridData.channels.length - 1, S.gridRow + 1); S.gridCol = -1; updateGridSel(); }
        else if (k === 'ArrowLeft') { S.gridCol > -1 ? gridMoveCol(-1) : (S.overlayFocus = 'menubar', buildMenuBar()); }
        else if (k === 'ArrowRight') gridMoveCol(1);
        else if (k === 'Enter') { hideOverlay(); gridSelect(); }
      } else {
        if (k === 'ArrowUp') overlayMove(-1);
        else if (k === 'ArrowDown') overlayMove(1);
        else if (k === 'ArrowLeft') { S.overlayFocus = 'menubar'; buildMenuBar(); }
        else if (k === 'Enter' && ovSelect) ovSelect(ovIdx);
      }
    } else {
      if (back) stopVideo();
      else if (k === 'ArrowUp') showOverlay();
      else if (k === 'ArrowDown' || k === 'Enter') trickMenu();
      else if (c === PLAYPAUSE || c === PLAY || c === PAUSE) { video.paused ? video.play().catch(() => {}) : video.pause(); }
      else if (c === FF || k === 'ArrowRight') { video.currentTime = Math.min(video.duration || 0, (video.currentTime || 0) + 30); }
      else if (c === RW || k === 'ArrowLeft') { video.currentTime = Math.max(0, (video.currentTime || 0) - 30); }
    }
    e.preventDefault();
    return;
  }

  // menu focus
  if (S.focus === 'menu') {
    if (k === 'ArrowUp') S.menuFocus = Math.max(0, S.menuFocus - 1);
    else if (k === 'ArrowDown') S.menuFocus = Math.min(S.tabs.length - 1, S.menuFocus + 1);
    else if (k === 'ArrowRight' || k === 'Enter') {
      clearTimeout(S.menuPreviewTimer);
      S.menuIdx = S.menuFocus;
      showTab(S.menuIdx);
      S.focus = S.mode === 'grid' ? 'grid' : 'content';
      renderMenu(); markSel();
      e.preventDefault(); return;
    }
    // Debounce the preview — an uncached /api/guide fetch per arrow press is
    // what made the menu feel flaky.
    renderMenu();
    clearTimeout(S.menuPreviewTimer);
    S.menuPreviewTimer = setTimeout(() => showTab(S.menuFocus), 350);
    e.preventDefault();
    return;
  }

  // list/content focus
  if (S.focus === 'content') {
    if (k === 'ArrowUp') { S.contentIdx = Math.max(0, S.contentIdx - 1); markSel(); }
    else if (k === 'ArrowDown') { S.contentIdx = Math.min(S.items.length - 1, S.contentIdx + 1); markSel(); }
    else if (k === 'ArrowLeft') { S.focus = 'menu'; S.menuFocus = S.menuIdx; renderMenu(); }
    else if (k === 'Enter') { /* handled on keyup so long-press can be options */ }
    else if (c === RED || k === '*') optionsAction();
    else if (c === TIZEN_BACK || k === 'Backspace' || k === 'Escape') { S.focus = 'menu'; renderMenu(); }
    e.preventDefault();
    return;
  }

  // guide grid focus
  if (S.focus === 'grid') {
    if (!S.gridData) { e.preventDefault(); return; }
    if (k === 'ArrowUp') { S.gridRow = Math.max(0, S.gridRow - 1); S.gridCol = -1; updateGridSel(); }
    else if (k === 'ArrowDown') { S.gridRow = Math.min(S.gridData.channels.length - 1, S.gridRow + 1); S.gridCol = -1; updateGridSel(); }
    else if (k === 'ArrowLeft') { if (S.gridCol <= -1) { S.focus = 'menu'; renderMenu(); } else gridMoveCol(-1); }
    else if (k === 'ArrowRight') gridMoveCol(1);
    else if (k === 'Enter') { /* keyup handles */ }
    else if (c === RED || k === '*') optionsAction();
    else if (c === TIZEN_BACK || k === 'Backspace' || k === 'Escape') { S.focus = 'menu'; renderMenu(); }
    e.preventDefault();
    return;
  }
});

// OK-key handling for content/grid focus. Samsung remotes have no * key, so
// long-press = options. Some TV browsers fire keyup unreliably for remote
// keys, so select works off three signals: quick keyup (tap), held-key
// repeats (long press), or a fallback timer (platforms with no keyup).
let okDownAt = 0, okPending = false, okLongDone = false;
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' || (S.focus !== 'content' && S.focus !== 'grid')) return;
  if (e.repeat) {
    if (okPending && Date.now() - okDownAt > 400) {
      okPending = false; okLongDone = true;
      optionsAction();
    }
    return;
  }
  okDownAt = Date.now(); okPending = true; okLongDone = false;
  clearTimeout(S.okFallbackTimer);
  S.okFallbackTimer = setTimeout(() => {
    if (okPending) { okPending = false; selectNow(); }
  }, 700);
});
document.addEventListener('keyup', e => {
  if (e.key !== 'Enter' || !okPending) return;
  okPending = false;
  clearTimeout(S.okFallbackTimer);
  if (!okLongDone) selectNow();
});
function selectNow() {
  if (S.focus === 'content') selectFocused();
  else if (S.focus === 'grid') gridSelect();
}

// ---------------------------------------------------------------- pointer / TV-cursor clicks
// The Samsung TV browser's remote drives a mouse cursor and OK = click, not a
// keydown. Every interactive element carries data-i so one delegated handler
// covers clicks for both the TV browser and a desktop mouse.

document.addEventListener('click', e => {
  const t = e.target;

  const db = t.closest('.dbtn');
  if (db && S.focus === 'dialog') { dialogChoose(+db.dataset.i); return; }

  const mi = t.closest('.menuitem');
  if (mi) {
    S.menuFocus = S.menuIdx = +mi.dataset.i;
    showTab(S.menuIdx);
    S.focus = S.mode === 'grid' ? 'grid' : 'content';
    renderMenu(); markSel();
    return;
  }

  const bar = t.closest('.baritem');
  if (bar && S.overlay) { applyOverlayTab(+bar.dataset.i); return; }

  const gc = t.closest('.gcell');
  if (gc) {
    const inOverlay = !!gc.closest('#overlay-pane');
    S.gridRow = +gc.dataset.ri; S.gridCol = +gc.dataset.ci;
    updateGridSel();
    if (inOverlay) hideOverlay();
    gridSelect();
    return;
  }
  const gch = t.closest('.gchan');
  if (gch) {
    const inOverlay = !!gch.closest('#overlay-pane');
    S.gridRow = +gch.dataset.ri; S.gridCol = -1;
    updateGridSel();
    if (inOverlay) hideOverlay();
    gridSelect();
    return;
  }

  const orow = t.closest('#overlay-pane .row');
  if (orow) { ovIdx = +orow.dataset.i; if (ovSelect) ovSelect(ovIdx); return; }

  const crow = t.closest('#content .row');
  if (crow) {
    S.contentIdx = +crow.dataset.i;
    S.focus = 'content';
    markSel();
    selectFocused();
    return;
  }

  if (t.closest('#video') && video.classList.contains('playing')) trickMenu();
});

// Trap the browser Back button (what the TV remote's Back sends in browser
// mode) so it acts as in-app Back instead of navigating away from the page.
try {
  history.pushState({ iptv: true }, '');
  window.addEventListener('popstate', () => {
    history.pushState({ iptv: true }, '');
    if (S.focus === 'dialog') closeDialog();
    else if (S.overlay) hideOverlay();
    else if (video.classList.contains('playing')) stopVideo();
    else if (S.focus !== 'menu') { S.focus = 'menu'; renderMenu(); }
  });
} catch (e) {}

// ---------------------------------------------------------------- boot

// Scale the fixed 1920x1080 design space to the actual viewport (TV browsers
// that ignore <meta viewport> report a smaller CSS size and crop otherwise).
function fitScreen() {
  const w = document.documentElement.clientWidth || 1920;
  const h = document.documentElement.clientHeight || 1080;
  const s = Math.min(w / 1920, h / 1080);
  const app = $('app');
  app.style.transform = `scale(${s})`;
  app.style.left = Math.floor((w - 1920 * s) / 2) + 'px';
  app.style.top = Math.floor((h - 1080 * s) / 2) + 'px';
}
window.addEventListener('resize', fitScreen);
fitScreen();

try {
  tizen.tvinputdevice.registerKeyBatch(
    ['MediaPlay', 'MediaPause', 'MediaPlayPause', 'MediaFastForward', 'MediaRewind', 'ColorF0Red', 'Info']);
} catch (e) {}

renderMenu();
if (!S.server) {
  promptServer();
} else {
  loadStatus();
  showTab(0);
}
S.statusTimer = setInterval(loadStatus, 30000);
