// Starlink satellite collection wired into murmuration.

import { Murmuration } from "../lib/murmuration.js";
import { makeTwoColorRamp, hexToRgb } from "../lib/color.js";
import { humanDuration } from "../lib/filters.js";
import { VARIANTS } from "./icons.js";

// palette hues (dark-surface steps)
const HUE = {
  blue: "#3987e5", orange: "#d95926", aqua: "#199e70",
  magenta: "#d55181", green: "#0ca30c",
};
const DIM = [0.29, 0.29, 0.28];       // never connected
const BRIGHT = [0.85, 0.84, 0.81];    // connected

// Default two-hue gradient endpoints (low -> high), user-editable by
// double-clicking a color chip; overrides persist in localStorage.
const DEFAULT_STOPS = {
  launched: ["#0d366b", "#55d6f5"],   // navy -> cyan
  incl: ["#6b3305", "#f2c14e"],       // umber -> gold
  alt: ["#5c1a2e", "#ff8fa3"],        // wine -> pink
  dwells: ["#7c2d12", "#fdba74"],     // rust -> light orange
  down: ["#1e1b4b", "#a5b4fc"],       // indigo -> periwinkle
  up: ["#500f42", "#f0abfc"],         // plum -> orchid
  total: ["#064e3b", "#6ee7b7"],      // forest -> mint
  seen: ["#312e81", "#34d399"],       // indigo -> emerald (recent = bright)
  ever: ["#4a4a48", "#d8d7cf"],       // dim gray -> bright gray
};
const STORAGE_KEY = "murmuration.colorStops";
const savedStops = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
const stopsFor = id => savedStops[id] ?? DEFAULT_STOPS[id];
const rampFor = id => makeTwoColorRamp(...stopsFor(id));

const fmtBytes = v => v < 1e6 ? `${(v / 1e3).toFixed(0)} kB`
  : v < 1e9 ? `${(v / 1e6).toFixed(1)} MB` : `${(v / 1e9).toFixed(2)} GB`;
const fmtDate = ms => new Date(ms).toISOString().slice(0, 10);
const fmtAgo = ms => `${humanDuration(Date.now() - ms)} ago`;
const shortType = t => t ? t.replace(" family (Gen2)", "").replace(" (Gen1)", "")
  .replace(" (prototype)", "") : "unknown";

const H = 3600e3, D = 24 * H;

// The ever-connected boolean colors both categories from its editable stops,
// so the closure must read the live facet object.
function everFacet() {
  const f = {
    id: "ever", label: "ever connected", type: "boolean", get: s => s.ever,
    trueLabel: "connected", falseLabel: "never", filterable: true,
    bucketable: true, colorable: true, openByDefault: true,
    colorStops: stopsFor("ever"), colorRamp: rampFor("ever"),
  };
  f.categoryColors = () => new Map([
    [true, hexToRgb(f.colorStops[1])], [false, hexToRgb(f.colorStops[0])]]);
  return f;
}

function makeFacets() {
  return [
    { id: "name", label: "name", type: "string", get: s => s.name, sortable: true },
    {
      id: "type", label: "type", type: "string", get: s => s.type,
      format: shortType, filterable: true, sortable: true, bucketable: true,
      colorable: true, openByDefault: true,
      categoryColors: () => new Map([
        ["v0.9 (prototype)", hexToRgb(HUE.magenta)],
        ["v1.0 (Gen1)", hexToRgb(HUE.aqua)],
        ["v1.5 (Gen1)", hexToRgb(HUE.blue)],
        ["V2 Mini family (Gen2)", hexToRgb(HUE.orange)],
      ]),
      // chip swatch only: discrete category colors
      colorRamp: t => hexToRgb(
        [HUE.magenta, HUE.aqua, HUE.blue, HUE.orange][Math.min(3, t * 4 | 0)]),
    },
    {
      id: "launched", label: "launch date", type: "datetime", get: s => s.launch_ms,
      format: fmtDate, filterable: true, sortable: true, bucketable: true,
      colorable: true, colorStops: stopsFor("launched"), colorRamp: rampFor("launched"),
    },
    {
      id: "incl", label: "inclination", type: "number", get: s => s.incl_deg,
      unit: "°", filterable: true, sortable: true, bucketable: true,
      colorable: true, colorStops: stopsFor("incl"), colorRamp: rampFor("incl"),
    },
    {
      id: "alt", label: "altitude", type: "number", get: s => s.alt_km,
      unit: "km", filterable: true, sortable: true, bucketable: true,
      colorable: true, colorStops: stopsFor("alt"), colorRamp: rampFor("alt"),
    },
    {
      id: "period", label: "orbital period", type: "number", get: s => s.period_min,
      unit: "min", filterable: true, sortable: true, bucketable: true,
    },
    {
      id: "dwells", label: "dwells", type: "number", get: s => s.dwells,
      filterable: true, sortable: true, bucketable: true, openByDefault: true,
      colorable: true, colorStops: stopsFor("dwells"), colorRamp: rampFor("dwells"),
    },
    {
      id: "slots", label: "slots", type: "number", get: s => s.slots,
      filterable: true, sortable: true, bucketable: true,
    },
    {
      id: "tracked", label: "tracked time", type: "number", get: s => s.seconds,
      format: v => v < 90 ? `${Math.round(v)} s` : humanDuration(v * 1000),
      filterable: true, sortable: true, bucketable: true,
    },
    {
      id: "down", label: "data down", type: "number", get: s => s.down_bytes,
      format: fmtBytes, scale: "log", filterable: true, sortable: true,
      bucketable: true, colorable: true,
      colorStops: stopsFor("down"), colorRamp: rampFor("down"),
    },
    {
      id: "up", label: "data up", type: "number", get: s => s.up_bytes,
      format: fmtBytes, scale: "log", filterable: true, sortable: true,
      bucketable: true, colorable: true,
      colorStops: stopsFor("up"), colorRamp: rampFor("up"),
    },
    {
      id: "total", label: "data total", type: "number",
      get: s => s.down_bytes + s.up_bytes,
      format: fmtBytes, scale: "log", filterable: true, sortable: true,
      bucketable: true, colorable: true,
      colorStops: stopsFor("total"), colorRamp: rampFor("total"),
    },
    {
      id: "seen", label: "last connected", type: "datetime", get: s => s.last_ms,
      format: fmtAgo, agoPresets: [H, 6 * H, D, 7 * D, 30 * D],
      filterable: true, sortable: true, bucketable: true, openByDefault: true,
      colorable: true,
      colorStops: stopsFor("seen"), colorRamp: rampFor("seen"),
    },
    everFacet(),
    {
      id: "current", label: "current satellite", type: "boolean",
      get: s => s.is_last, trueLabel: "current", falseLabel: "other",
      filterable: true,
    },
    {
      id: "dtc", label: "direct-to-cell", type: "boolean", get: s => s.dtc,
      trueLabel: "DTC", falseLabel: "broadband only",
      filterable: true, bucketable: true,
    },
    {
      id: "status", label: "status", type: "string", get: s => s.status,
      filterable: true, bucketable: true,
    },
    {
      id: "site", label: "launch site", type: "string", get: s => s.launch_site,
      format: v => v.includes("Vandenberg") ? "Vandenberg"
        : v.includes("Eastern") ? "Cape Canaveral" : v,
      filterable: true, bucketable: true,
    },
  ];
}

function detailRows(s) {
  const rows = [
    ["name", s.name], ["NORAD", String(s.norad)], ["intl", s.intl ?? "—"],
    ["type", shortType(s.type) + (s.dtc ? " · DTC" : "")],
    ["launched", s.launch_ms ? `${fmtDate(s.launch_ms)} · ${s.launch_site?.split(" (")[0] ?? ""}` : "—"],
    ["status", s.status ?? "—"],
    ["orbit", `${s.perigee_km} × ${s.apogee_km} km · ${s.incl_deg}° · ${s.period_min} min`],
  ];
  if (s.ever) {
    rows.push(
      ["dwells", String(s.dwells)], ["slots", String(s.slots)],
      ["tracked", `${Math.round(s.seconds)} s`],
      ["data ↓", fmtBytes(s.down_bytes)], ["data ↑", fmtBytes(s.up_bytes)],
      ["last seen", `${fmtAgo(s.last_ms)}`],
    );
  } else {
    rows.push(["history", "never connected"]);
  }
  if (s.is_last) rows.push(["●", "most recently connected satellite"]);
  return rows;
}

async function boot() {
  const res = await fetch("satellites.json");
  const doc = await res.json();
  const items = doc.satellites.map(s => ({
    ...s,
    launch_ms: s.launch_date ? Date.parse(s.launch_date) : null,
    last_ms: s.last_seen ? Date.parse(s.last_seen) : null,
    ever: s.dwells > 0,
    is_last: s.norad === doc.last_connected_norad,
  }));

  const mm = new Murmuration({
    container: document.getElementById("app"),
    items,
    facets: makeFacets(),
    idOf: s => s.norad,
    variantOf: s => !s.type ? "unknown" : s.type.includes("V2") ? "v2" : "v1",
    variants: VARIANTS,
    searchText: s => `${s.name} ${s.norad}`,
    tooltipText: s => `${s.name} · ${shortType(s.type)}` +
      (s.ever ? ` · ${s.dwells} dwell${s.dwells === 1 ? "" : "s"} · ↓${fmtBytes(s.down_bytes)}`
        : " · never connected"),
    detailRows,
    baseColor: s => s.is_last ? hexToRgb(HUE.green) : s.ever ? BRIGHT : DIM,
    defaults: { view: "grid", sortId: "launched", bucketId: "type" },
    onColorStops: (id, stops) => {
      savedStops[id] = stops;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(savedStops));
    },
  });
  mm.setPulse(doc.last_connected_norad);

  const meta = document.getElementById("meta");
  meta.textContent =
    `${doc.count.toLocaleString()} satellites · ${doc.connected_count.toLocaleString()} connected · ` +
    `catalogue ${doc.catalog} ${doc.catalog_age_hours} h old · exported ${doc.generated}`;
  const liveSpan = document.createElement("span");
  meta.append(liveSpan);

  // Live current-satellite: satmatch --dwells publishes current.json on
  // every confident observation; poll it and move the green pulse.
  let currentNorad = doc.last_connected_norad;
  async function pollCurrent() {
    let cur;
    try {
      const res = await fetch("current.json", { cache: "no-store" });
      if (!res.ok) return;
      cur = await res.json();
    } catch { return; }   // no file yet — keep the export's last-connected
    const fresh = Date.now() - Date.parse(cur.updated) < 90e3;
    liveSpan.textContent = fresh
      ? ` · ● live: ${cur.name}`
      : ` · last seen: ${cur.name} (${cur.updated.slice(11, 16)} UTC)`;
    liveSpan.style.color = fresh ? "#0ca30c" : "";
    if (cur.norad !== currentNorad) {
      currentNorad = cur.norad;
      for (const it of items) it.is_last = it.norad === cur.norad;
      mm.setPulse(cur.norad);
      mm.refresh();       // recolor + update "current satellite" facet
    }
  }
  setInterval(pollCurrent, 5000);
  pollCurrent();
}

boot().catch(err => {
  document.getElementById("meta").textContent = `failed to load: ${err.message}`;
  console.error(err);
});
