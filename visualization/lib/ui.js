// DOM chrome around the canvas: top bar (view switcher, sort, bucket,
// color-by chips), left filter pane, breadcrumbs, right legend + detail pane,
// bucket labels, tooltip. All plain DOM; murmuration.js supplies callbacks.

import { facetValue, formatValue, extent } from "./facets.js";
import { rgbToHex } from "./color.js";
import { humanDuration } from "./filters.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class UI {
  constructor(container, facets, callbacks, views = ["grid", "graph"]) {
    this.facets = facets;
    this.cb = callbacks;
    this.views = views;
    container.classList.add("mm-root");

    this.topbar = el("div", "mm-topbar");
    this.main = el("div", "mm-main");
    container.append(this.topbar, this.main);

    this.filterPane = el("div", "mm-filterpane");
    this.canvasWrap = el("div", "mm-canvas-wrap");
    this.rightPane = el("div", "mm-rightpane");
    this.main.append(this.filterPane, this.canvasWrap, this.rightPane);

    this.canvas = el("canvas", "mm-canvas");
    this.labelLayer = el("div", "mm-labels");
    this.crumbs = el("div", "mm-breadcrumbs");
    this.tooltip = el("div", "mm-tooltip");
    this.tooltip.style.display = "none";
    this.canvasWrap.append(this.canvas, this.labelLayer, this.crumbs, this.tooltip);

    this.legend = el("div", "mm-legend");
    this.detail = el("div", "mm-detail");
    this.rightPane.append(this.legend, this.detail);

    this._buildTopbar();
    this._buildFilterPane();
  }

  // ------------------------------------------------------------ top bar

  _buildTopbar() {
    this.viewButtons = {};
    const views = el("div", "mm-views");
    for (const v of this.views) {
      const b = el("button", "mm-viewbtn", v[0].toUpperCase() + v.slice(1));
      b.onclick = () => this.cb.onView(v);
      views.append(b);
      this.viewButtons[v] = b;
    }

    const sortWrap = el("label", "mm-ctl", "sort ");
    this.sortSelect = document.createElement("select");
    for (const f of this.facets.filter(f => f.sortable)) {
      this.sortSelect.append(new Option(f.label, f.id));
    }
    this.sortSelect.onchange = () => this.cb.onSort(this.sortSelect.value);
    this.sortDir = el("button", "mm-dirbtn", "↑");
    this.sortDir.onclick = () => this.cb.onSortDir();
    sortWrap.append(this.sortSelect, this.sortDir);

    this.bucketWrap = el("label", "mm-ctl", "by ");
    this.bucketSelect = document.createElement("select");
    for (const f of this.facets.filter(f => f.bucketable)) {
      this.bucketSelect.append(new Option(f.label, f.id));
    }
    this.bucketSelect.onchange = () => this.cb.onBucket(this.bucketSelect.value);
    this.bucketWrap.append(this.bucketSelect);

    const colorWrap = el("div", "mm-colorby");
    colorWrap.append(el("span", "mm-ctl-label", "color"));
    this.colorChips = {};
    for (const f of this.facets.filter(f => f.colorable)) {
      const chip = el("button", "mm-chip", f.label);
      if (f.colorStops) chip.title = "double-click to edit colors";
      const swatch = el("span", "mm-chip-swatch");
      chip.prepend(swatch);
      // single click toggles; a double click edits instead (guarded delay)
      let clickTimer = null;
      chip.onclick = () => {
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => this.cb.onColorToggle(f.id), 260);
      };
      chip.ondblclick = () => {
        clearTimeout(clickTimer);
        if (f.colorStops) this._openColorEditor(f, chip);
      };
      colorWrap.append(chip);
      this.colorChips[f.id] = { chip, swatch, facet: f };
      this.updateChipSwatch(f);
    }

    this.countLabel = el("div", "mm-count");
    this.topbar.append(views, sortWrap, this.bucketWrap, colorWrap, this.countLabel);
  }

  syncTopbar({ view, sortId, sortDir, bucketId, colorIds, shown, total }) {
    for (const [v, b] of Object.entries(this.viewButtons)) {
      b.classList.toggle("active", v === view);
    }
    this.sortSelect.value = sortId;
    this.sortDir.textContent = sortDir > 0 ? "↑" : "↓";
    this.bucketSelect.value = bucketId;
    this.bucketWrap.style.visibility = view === "graph" ? "visible" : "hidden";
    for (const [id, { chip }] of Object.entries(this.colorChips)) {
      chip.classList.toggle("active", colorIds.includes(id));
    }
    this.countLabel.textContent = `${shown.toLocaleString()} / ${total.toLocaleString()}`;
  }

  updateChipSwatch(facet) {
    const entry = this.colorChips[facet.id];
    if (!entry || !facet.colorRamp) return;
    const stops = [0, 0.25, 0.5, 0.75, 1]
      .map(t => `${rgbToHex(facet.colorRamp(t))} ${t * 100}%`).join(",");
    entry.swatch.style.background = `linear-gradient(90deg,${stops})`;
  }

  _openColorEditor(facet, anchor) {
    this._closeColorEditor();
    const pop = el("div", "mm-colorpop");
    const inputs = facet.colorStops.map(hex => {
      const inp = document.createElement("input");
      inp.type = "color";
      inp.value = hex;
      inp.oninput = () => this.cb.onColorStops(facet.id,
        [inputs[0].value, inputs[1].value]);
      return inp;
    });
    pop.append(el("span", "mm-colorpop-label", "low"), inputs[0],
      el("span", "mm-colorpop-label", "high"), inputs[1]);
    const r = anchor.getBoundingClientRect();
    pop.style.left = `${r.left}px`;
    pop.style.top = `${r.bottom + 6}px`;
    document.body.append(pop);
    this._colorPop = pop;
    setTimeout(() => {
      this._dismissEditor = e => {
        if (!pop.contains(e.target)) this._closeColorEditor();
      };
      document.addEventListener("pointerdown", this._dismissEditor);
    });
  }

  _closeColorEditor() {
    this._colorPop?.remove();
    this._colorPop = null;
    if (this._dismissEditor) {
      document.removeEventListener("pointerdown", this._dismissEditor);
      this._dismissEditor = null;
    }
  }

  // --------------------------------------------------------- filter pane

  _buildFilterPane() {
    this.search = document.createElement("input");
    this.search.type = "search";
    this.search.placeholder = "search name…";
    this.search.className = "mm-search";
    this.search.oninput = () => this.cb.onSearch(this.search.value.trim());
    this.filterPane.append(this.search);

    this.facetSections = {};
    for (const f of this.facets.filter(f => f.filterable)) {
      const sec = document.createElement("details");
      sec.className = "mm-facet";
      sec.open = !!f.openByDefault;
      const sum = el("summary", null, f.label);
      const body = el("div", "mm-facet-body");
      sec.append(sum, body);
      this.filterPane.append(sec);
      this.facetSections[f.id] = { sec, body, facet: f };
    }
  }

  /** Rebuild filter widgets: counts respect all *other* active filters. */
  renderFilters(items, filters, countsFor) {
    for (const { body, facet } of Object.values(this.facetSections)) {
      body.textContent = "";
      const desc = filters.get(facet.id);
      if (facet.type === "string" || facet.type === "boolean") {
        this._optionList(body, facet, desc, countsFor(facet));
      } else if (facet.type === "datetime" && facet.agoPresets) {
        this._agoPresets(body, facet, desc);
      } else {
        this._rangeSlider(body, facet, desc, items);
      }
    }
  }

  _optionList(body, facet, desc, counts) {
    const values = facet.type === "boolean"
      ? [true, false]
      : [...counts.keys()].filter(v => v !== null).sort();
    for (const v of values) {
      const row = el("label", "mm-option");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = facet.type === "boolean"
        ? desc?.value === v
        : desc?.values?.has(v) ?? false;
      box.onchange = () => facet.type === "boolean"
        ? this.cb.onBoolFilter(facet.id, box.checked ? v : null)
        : this.cb.onStringToggle(facet.id, v);
      row.append(box, el("span", "mm-option-label", formatValue(facet, v)),
        el("span", "mm-option-count", String(counts.get(v) ?? 0)));
      body.append(row);
    }
  }

  _agoPresets(body, facet, desc) {
    const mk = (label, withinMs) => {
      const row = el("label", "mm-option");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = `mm-ago-${facet.id}`;
      radio.checked = withinMs === null
        ? desc === undefined
        : desc?.withinMs === withinMs;
      radio.onchange = () => this.cb.onAgoFilter(facet.id, withinMs);
      row.append(radio, el("span", "mm-option-label", label));
      body.append(row);
    };
    mk("any time", null);
    for (const ms of facet.agoPresets) mk(`last ${humanDuration(ms)}`, ms);
  }

  /** Numeric/datetime filter: a mini histogram of the value distribution
   *  with min/max sliders below it; bars inside the selection highlight. */
  _rangeSlider(body, facet, desc, items) {
    const ext = extent(facet, items);
    if (!ext) { body.append(el("div", "mm-option-label", "no data")); return; }
    const [lo, hi] = ext;
    const toT = v => normalizeLinear(facet, v, lo, hi);
    const fromT = t => denormalizeLinear(facet, t, lo, hi);
    const cur = { min: desc?.min ?? lo, max: desc?.max ?? hi };

    // one bin per integer for small integer ranges (dwells, slots), else 28
    const integer = Number.isInteger(lo) && Number.isInteger(hi)
      && facet.scale !== "log" && hi - lo + 1 <= 28;
    const BINS = integer ? hi - lo + 1 : 28;
    const counts = new Array(BINS).fill(0);
    for (const item of items) {
      const v = facetValue(facet, item);
      if (v === null) continue;
      counts[Math.min(BINS - 1, Math.floor(toT(v) * BINS))]++;
    }
    const maxCount = Math.max(...counts, 1);

    const W = 220, HGT = 40, dpr = devicePixelRatio || 1;
    const canvas = document.createElement("canvas");
    canvas.className = "mm-histo";
    canvas.width = W * dpr; canvas.height = HGT * dpr;
    const ctx = canvas.getContext("2d");
    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const [tLo, tHi] = [toT(cur.min), toT(cur.max)];
      const barW = canvas.width / BINS;
      counts.forEach((c, i) => {
        if (!c) return;
        const mid = (i + 0.5) / BINS;
        // sqrt scale so one dominant bin doesn't flatten the rest
        const h = Math.max(2 * dpr, Math.sqrt(c / maxCount) * canvas.height);
        ctx.fillStyle = mid >= tLo && mid <= tHi ? "#3987e5" : "#3f3f3d";
        ctx.fillRect(i * barW + 0.5 * dpr, canvas.height - h,
          barW - 1 * dpr, h);
      });
    };
    draw();

    const wrap = el("div", "mm-range");
    const readout = el("div", "mm-range-readout");
    const sliders = ["min", "max"].map(which => {
      const s = document.createElement("input");
      s.type = "range";
      s.min = 0; s.max = 1000;
      s.value = Math.round(toT(cur[which]) * 1000);
      s.oninput = () => {
        cur[which] = fromT(+s.value / 1000);
        if (cur.min > cur.max) [cur.min, cur.max] = [cur.max, cur.min];
        readout.textContent = `${formatValue(facet, cur.min)} – ${formatValue(facet, cur.max)}`;
        draw();
      };
      s.onchange = () => this.cb.onRangeFilter(facet.id,
        cur.min <= lo && cur.max >= hi ? null : { min: cur.min, max: cur.max });
      return s;
    });
    readout.textContent = `${formatValue(facet, cur.min)} – ${formatValue(facet, cur.max)}`;
    wrap.append(canvas, ...sliders, readout);
    body.append(wrap);
  }

  // ----------------------------------------------------- chips / legend

  renderChips(chips) {
    this.crumbs.textContent = "";
    if (!chips.length) return;
    for (const c of chips) {
      const chip = el("button", "mm-crumb", c.label + " ✕");
      chip.onclick = () => this.cb.onClearFilter(c.facetId);
      this.crumbs.append(chip);
    }
    const clear = el("button", "mm-crumb mm-crumb-clear", "clear all");
    clear.onclick = () => this.cb.onClearAll();
    this.crumbs.append(clear);
  }

  renderLegend(colorFacets, items) {
    this.legend.textContent = "";
    for (const f of colorFacets) {
      const block = el("div", "mm-legend-block");
      block.append(el("div", "mm-legend-title", f.label));
      if (f.type === "string" || f.type === "boolean") {
        for (const [value, rgb] of f.categoryColors(items)) {
          const row = el("div", "mm-legend-row");
          const sw = el("span", "mm-swatch");
          sw.style.background = rgbToHex(rgb);
          row.append(sw, el("span", null, formatValue(f, value)));
          block.append(row);
        }
      } else {
        const bar = el("div", "mm-legend-bar");
        const stops = [0, 0.25, 0.5, 0.75, 1]
          .map(t => `${rgbToHex(f.colorRamp(f.invertColor ? 1 - t : t))} ${t * 100}%`).join(",");
        bar.style.background = `linear-gradient(90deg,${stops})`;
        const ext = extent(f, items);
        const lab = el("div", "mm-legend-minmax");
        lab.append(el("span", null, ext ? formatValue(f, ext[0]) : "—"),
          el("span", null, ext ? formatValue(f, ext[1]) : "—"));
        block.append(bar, lab);
      }
      this.legend.append(block);
    }
  }

  // ------------------------------------------------------ detail / labels

  renderDetail(item, rows) {
    this.detail.textContent = "";
    if (!item) {
      this.detail.append(el("div", "mm-detail-empty",
        "click a satellite for details"));
      return;
    }
    for (const [label, value] of rows) {
      const row = el("div", "mm-detail-row");
      row.append(el("span", "mm-detail-label", label),
        el("span", "mm-detail-value", value));
      this.detail.append(row);
    }
  }

  updateLabels(labels, project) {
    if (this._labelCount !== labels.length) {
      this.labelLayer.textContent = "";
      this._labelDivs = labels.map(() => {
        const d = el("div", "mm-bucket-label");
        this.labelLayer.append(d);
        return d;
      });
      this._labelCount = labels.length;
    }
    labels.forEach((lab, i) => {
      const d = this._labelDivs[i];
      const p = project(lab.x, lab.y);
      d.style.transform = `translate(-50%,-50%) translate(${p.x}px,${p.y}px)`;
      if (d._text !== lab.text) {
        d.textContent = "";
        d.append(el("span", null, lab.text), el("span", "mm-bucket-count", ` ${lab.count}`));
        d._text = lab.text;
      }
    });
  }

  showTooltip(text, clientX, clientY) {
    if (!text) { this.tooltip.style.display = "none"; return; }
    this.tooltip.style.display = "block";
    this.tooltip.textContent = text;
    const r = this.canvasWrap.getBoundingClientRect();
    this.tooltip.style.left = `${clientX - r.left + 14}px`;
    this.tooltip.style.top = `${clientY - r.top + 14}px`;
  }
}

// slider position mapping (log-aware)
function normalizeLinear(facet, v, lo, hi) {
  if (facet.scale === "log") {
    const a = Math.log1p(lo), b = Math.log1p(hi);
    return b > a ? (Math.log1p(v) - a) / (b - a) : 0.5;
  }
  return hi > lo ? (v - lo) / (hi - lo) : 0.5;
}

function denormalizeLinear(facet, t, lo, hi) {
  if (facet.scale === "log") {
    const a = Math.log1p(lo), b = Math.log1p(hi);
    return Math.expm1(a + t * (b - a));
  }
  return lo + t * (hi - lo);
}
