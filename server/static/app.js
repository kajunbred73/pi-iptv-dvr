const $ = (s, el = document) => el.querySelector(s);
const api = (path, opts = {}) => fetch("/api" + path, {
  headers: { "Content-Type": "application/json" }, ...opts,
  body: opts.body ? JSON.stringify(opts.body) : undefined,
}).then(r => r.json());

const fmt = ts => new Date(ts * 1000).toLocaleString([], { weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const fmtSize = b => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(0) + " MB";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let channels = [];

// ---- tabs
document.querySelectorAll("nav a").forEach(a => a.onclick = e => {
  e.preventDefault();
  document.querySelectorAll("nav a, .tab").forEach(x => x.classList.remove("active"));
  a.classList.add("active");
  $("#tab-" + a.dataset.tab).classList.add("active");
  loaders[a.dataset.tab]?.();
});

// ---- channels
async function loadChannels() {
  channels = await api("/channels");
  const groups = await api("/groups");
  const sel = $("#chan-group");
  sel.innerHTML = '<option value="">All groups</option>' + groups.map(g => `<option>${esc(g)}</option>`).join("");
  $("#guide-chan").innerHTML = channels.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  renderChannels();
}
function renderChannels() {
  const f = $("#chan-filter").value.toLowerCase(), g = $("#chan-group").value;
  const rows = channels.filter(c => (!g || c.grp === g) && (!f || c.name.toLowerCase().includes(f)));
  $("#chan-table tbody").innerHTML = rows.slice(0, 500).map(c => `
    <tr>
      <td>${c.num}</td>
      <td>${c.logo ? `<img src="${esc(c.logo)}" loading="lazy">` : ""}</td>
      <td>${c.favorite ? "★ " : ""}${esc(c.name)}<br><span class="muted">${esc(c.grp)}</span></td>
      <td>${c.now ? `${esc(c.now.title)}<br><span class="muted">${fmtTime(c.now.start)}–${fmtTime(c.now.stop)}</span>` : '<span class="muted">no guide data</span>'}</td>
      <td>${c.next ? esc(c.next.title) : ""}</td>
      <td>
        <a href="${c.stream_url}"><button class="secondary">Play</button></a>
        <button onclick="recordNow(${c.id})">Rec ${c.now ? "show" : "60m"}</button>
        <button class="secondary" onclick="fav(${c.id}, ${c.favorite ? 0 : 1})">${c.favorite ? "Unfav" : "Fav"}</button>
      </td>
    </tr>`).join("");
}
$("#chan-filter").oninput = renderChannels;
$("#chan-group").onchange = renderChannels;

window.recordNow = async id => {
  const c = channels.find(x => x.id === id);
  const body = c.now ? { channel_id: id, program_start: c.now.start } : { channel_id: id, minutes: 60 };
  await api("/schedules", { method: "POST", body });
  alert("Recording scheduled");
};
window.fav = async (id, v) => { await api(`/channels/${id}/favorite`, { method: "POST", body: { favorite: !!v } }); loadChannels(); };

// ---- guide
async function loadGuide() {
  const cid = $("#guide-chan").value;
  if (!cid) return;
  const g = await api(`/epg/${cid}?hours=48`);
  $("#guide-table tbody").innerHTML = g.programs.map(p => `
    <tr>
      <td>${fmt(p.start)}</td><td>${fmtTime(p.stop)}</td>
      <td>${esc(p.title)}<br><span class="muted">${esc(p.description).slice(0, 160)}</span></td>
      <td>${p.scheduled ? '<span class="badge scheduled">scheduled</span>' : `<button onclick="recProg(${cid}, ${p.start})">Record</button>`}</td>
    </tr>`).join("") || '<tr><td colspan=4 class="muted">No guide data for this channel</td></tr>';
}
$("#guide-chan").onchange = loadGuide;
window.recProg = async (cid, start) => { await api("/schedules", { method: "POST", body: { channel_id: cid, program_start: start } }); loadGuide(); };

// ---- schedules
async function loadSchedules() {
  const s = await api("/schedules");
  $("#sched-table tbody").innerHTML = s.map(x => `
    <tr><td>${esc(x.channel_name)}</td><td>${esc(x.title)}</td><td>${fmt(x.start)}</td><td>${fmtTime(x.stop)}</td>
    <td><span class="badge ${x.status}">${x.status}</span></td>
    <td><button class="danger" onclick="cancelSched(${x.id})">Cancel</button></td></tr>`).join("") || '<tr><td colspan=6 class="muted">Nothing scheduled</td></tr>';
}
window.cancelSched = async id => { await api(`/schedules/${id}`, { method: "DELETE" }); loadSchedules(); };

// ---- recordings
async function loadRecordings() {
  const r = await api("/recordings");
  $("#rec-table tbody").innerHTML = r.map(x => `
    <tr><td>${esc(x.title)}<br><span class="muted">${esc(x.description).slice(0, 120)}</span></td><td>${esc(x.channel_name)}</td>
    <td>${fmt(x.start)}</td><td>${fmtSize(x.size_bytes)}</td>
    <td><span class="badge ${x.status}">${x.status}</span></td>
    <td><a href="${x.stream_url}"><button class="secondary">Open</button></a>
        <button class="danger" onclick="delRec(${x.id})">Delete</button></td></tr>`).join("") || '<tr><td colspan=6 class="muted">No recordings yet</td></tr>';
}
window.delRec = async id => { if (confirm("Delete recording?")) { await api(`/recordings/${id}`, { method: "DELETE" }); loadRecordings(); } };

// ---- settings / status
$("#server-addr").textContent = location.host;
$("#refresh-btn").onclick = async () => { await api("/refresh", { method: "POST" }); loadStatus(); };
let wasImporting = false;
async function loadStatus() {
  const s = await api("/status");
  const imp = s.import || {};
  let text;
  if (imp.running) {
    text = `Importing ${imp.step}… (${Math.round((Date.now() / 1000) - imp.started)}s) — large guides can take several minutes on a Pi`;
    wasImporting = true;
    setTimeout(loadStatus, 3000);
  } else {
    text = `${s.channels} channels · ${s.programs} programs · EPG ${s.epg_last ? fmt(s.epg_last) : "never"}` +
      (s.live_sessions.length ? ` · streaming: ${s.live_sessions.map(l => l.name).join(", ")}` : "");
    const err = imp.result && (imp.result.channels_error || imp.result.programs_error);
    if (err) text = `Import error: ${err} · ` + text;
    if (wasImporting) { wasImporting = false; loadChannels(); }
  }
  $("#status").textContent = text;
}
const loaders = { channels: loadChannels, guide: loadGuide, schedules: loadSchedules, recordings: loadRecordings };
loadStatus(); loadChannels();
setInterval(loadStatus, 15000);
const startTab = new URLSearchParams(location.search).get("tab");
if (startTab) document.querySelector(`nav a[data-tab="${startTab}"]`)?.click();
