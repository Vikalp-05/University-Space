"use strict";

const searchEl = document.getElementById("search");
const buildingEl = document.getElementById("building");
const messageEl = document.getElementById("message");
const roomsEl = document.getElementById("rooms");

let requestId = 0;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LABEL = { free: "Free", busy: "Busy", unknown: "?" };

async function api(path) {
  const res = await fetch(path);
  let body = null;
  try { body = await res.json(); } catch { /* ignore */ }
  if (!res.ok) throw new Error((body && typeof body.detail === "string" && body.detail) || res.statusText);
  return body;
}

function setMessage(html, isError = false) {
  messageEl.innerHTML = html;
  messageEl.classList.toggle("error", isError);
}

// ---------- building filter ----------

async function loadBuildings() {
  let campuses;
  try {
    campuses = await api("/api/campuses");
  } catch (e) {
    setMessage(`Couldn't load the room list: ${esc(e.message)}`, true);
    return;
  }
  const selected = buildingEl.value;
  buildingEl.innerHTML = `<option value="">All buildings</option>`;
  campuses.forEach((c) => {
    const group = document.createElement("optgroup");
    group.label = c.name;
    c.buildings.forEach((b) => {
      const label = b.name ? `${b.code} – ${b.name}` : `Building ${b.code}`;
      group.appendChild(new Option(label, `${c.code}:${b.code}`));
    });
    buildingEl.appendChild(group);
  });
  buildingEl.value = selected;
}

// ---------- rooms ----------

async function refresh() {
  const q = searchEl.value.trim();
  const [campus, building] = buildingEl.value.split(":");
  const id = ++requestId;

  if (!q && !building) {
    roomsEl.innerHTML = "";
    setMessage("Search for a room or pick a building.");
    return;
  }

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (building) { params.set("campus", campus); params.set("building", building); }

  setMessage("Checking…");
  try {
    const data = await api(`/api/status?${params}`);
    if (id !== requestId) return; // a newer search has started
    roomsEl.innerHTML = data.rooms.map((r) => `
      <div class="card ${r.status}">
        <span class="code">${esc(r.room.code)}</span>
        <span class="pill ${r.status}">${LABEL[r.status]}</span>
      </div>`).join("");
    setMessage(data.counts.total
      ? `<strong>${data.counts.free}</strong> of ${data.counts.total} rooms free right now`
      : "No rooms match.");
    if (building) loadBuildings(); // building names are learned as timetables load
  } catch (e) {
    if (id !== requestId) return;
    roomsEl.innerHTML = "";
    setMessage(esc(e.message), true);
  }
}

let timer;
searchEl.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 300); });
buildingEl.addEventListener("change", refresh);

// Keep statuses current while the page is open.
setInterval(() => { if (!document.hidden) refresh(); }, 5 * 60 * 1000);

loadBuildings();
refresh();