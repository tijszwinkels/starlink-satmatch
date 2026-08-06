// murmuration — a Pivot-style faceted visualization: a flock of items that
// fly between layouts as you filter, sort, bucket and recolor them.
// (A murmuration is a flock of starlings moving as one.)
//
// new Murmuration({
//   container, items, facets,
//   idOf(item), variantOf(item) -> icon variant key,
//   variants: [{key, image}],           // icon textures (canvas/img)
//   searchText(item) -> string,         // corpus for the search box
//   tooltipText(item) -> string,
//   detailRows(item) -> [[label, value], ...],
//   baseColor(item) -> [r,g,b],         // color when no color facet active
//   defaults: {sortId, bucketId, view, colorIds},
// })

import { compareBy, extent, facetValue, formatValue, makeBuckets, groupByBuckets, normalize } from "./facets.js";
import { createFilters, setFilter, toggleStringValue, predicate, optionCounts, filterChips } from "./filters.js";
import { gridLayout, histogramLayout, hitTest } from "./layouts.js";
import { Animator, choreograph, recolor, TIMING } from "./animate.js";
import { blendRgb, makeTwoColorRamp } from "./color.js";
import { Renderer } from "./renderer.js";
import { UI } from "./ui.js";

const MISSING_COLOR = [0.28, 0.28, 0.27];

export class Murmuration {
  constructor(opts) {
    this.opts = opts;
    this.items = opts.items;
    this.facets = opts.facets;
    this.byId = new Map(opts.facets.map(f => [f.id, f]));
    this.itemById = new Map(opts.items.map(i => [opts.idOf(i), i]));

    this.state = {
      view: opts.defaults?.view ?? "grid",
      sortId: opts.defaults?.sortId ?? opts.facets.find(f => f.sortable).id,
      sortDir: 1,
      bucketId: opts.defaults?.bucketId ?? opts.facets.find(f => f.bucketable).id,
      colorIds: opts.defaults?.colorIds ?? [],
      filters: createFilters(),
      hoverId: null,
      selectId: null,
      pulseId: null,
    };

    this.anim = new Animator();
    this._extents = new Map();
    this._categoryColors = new Map();

    this.ui = new UI(opts.container, opts.facets, this._callbacks(),
      [...(opts.orbitView ? ["orbit"] : []), "graph", "grid"]);
    const variantCounts = new Map();
    for (const item of this.items) {
      const v = opts.variantOf(item);
      variantCounts.set(v, (variantCounts.get(v) ?? 0) + 1);
    }
    this.renderer = new Renderer(this.ui.canvas, {
      variants: opts.variants.map(v => ({ ...v, capacity: variantCounts.get(v.key) ?? 0 })),
    });
    this.renderer.assignSlots(this.items.map(i => ({
      id: opts.idOf(i), variant: opts.variantOf(i),
    })));

    this._bindPointer();
    new ResizeObserver(() => {
      this.renderer.resize();
      this.update({ animate: false });
    }).observe(this.ui.canvas);
    this.renderer.resize();
    this.update({ animate: false });
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  // ------------------------------------------------------------- pipeline

  visibleItems() {
    const pred = predicate(this.byId, this.state.filters, {
      search: (item, text) =>
        this.opts.searchText(item).toLowerCase().includes(text.toLowerCase()),
    });
    return this.items.filter(pred);
  }

  update({ animate = true } = {}) {
    const now = performance.now();
    const s = this.state;
    const visible = this.visibleItems();
    const sortFacet = this.byId.get(s.sortId);
    visible.sort(compareBy(sortFacet, s.sortDir));
    const ids = visible.map(this.opts.idOf);
    const aspect = Math.max(0.2,
      this.ui.canvas.clientWidth / Math.max(1, this.ui.canvas.clientHeight));

    let layout;
    if (s.view === "orbit" && this.opts.orbitView) {
      if (!this._orbitSetup) {
        this.opts.orbitView.setup(this.renderer.scene);
        Object.assign(this.renderer.orbit, this.opts.orbitView.camera?.() ?? {});
        this._orbitSetup = true;
      }
      layout = {
        positions: this.opts.orbitView.positions(visible, now),
        bounds: null, labels: [], columns: [],
      };
    } else if (s.view === "graph") {
      const bucketFacet = this.byId.get(s.bucketId);
      const buckets = makeBuckets(bucketFacet, visible);
      const groups = groupByBuckets(bucketFacet, buckets, visible)
        .map(g => ({ label: g.bucket.label, ids: g.items.map(this.opts.idOf) }));
      layout = histogramLayout(groups, { aspect });
    } else {
      layout = gridLayout(ids, { aspect });
    }
    this.layout = layout;
    this.renderer.setColumns(layout.columns);

    const b = layout.bounds;
    const exitRadius = b
      ? Math.hypot(b.maxX - b.minX, b.maxY - b.minY) * 0.8 : 3000;
    const prevVisible = new Set(this.anim.items.keys());
    const { leavers } = choreograph(this.anim, prevVisible, layout.positions, {
      now, exitRadius,
      timing: animate ? TIMING : { ...TIMING, exitMs: 1, moveMs: 1, staggerMs: 0 },
    });
    const delay = animate && leavers.length ? TIMING.staggerMs : 0;
    if (s.view === "orbit") {
      this.renderer.enterOrbit({ delay, duration: TIMING.moveMs, now, animate });
      if (this._shownView !== "orbit") this.opts.orbitView.enter();
    } else {
      this.renderer.fitBounds(b, { animate, delay, duration: TIMING.moveMs, now });
      if (this._shownView === "orbit") this.opts.orbitView.leave();
    }
    this._shownView = s.view;
    recolor(this.anim, this._colorsFor(visible), {
      now, timing: animate ? TIMING : { ...TIMING, colorMs: 1 },
    });

    this._syncUi(visible);
  }

  _syncUi(visible) {
    const s = this.state;
    this.ui.syncTopbar({
      view: s.view, sortId: s.sortId, sortDir: s.sortDir, bucketId: s.bucketId,
      colorIds: s.colorIds, shown: visible.length, total: this.items.length,
    });
    this.ui.renderFilters(this.items, s.filters, facet =>
      optionCounts(facet, this.items,
        predicate(this.byId, s.filters, {
          skipFacetId: facet.id,
          search: (item, text) =>
            this.opts.searchText(item).toLowerCase().includes(text.toLowerCase()),
        })));
    this.ui.renderChips(filterChips(this.byId, s.filters, formatValue));
    this.ui.renderLegend(
      s.colorIds.map(id => this._legendFacet(id)), this.items);
    const sel = s.selectId !== null ? this.itemById.get(s.selectId) : null;
    this.ui.renderDetail(sel, sel ? this.opts.detailRows(sel) : null);
  }

  // -------------------------------------------------------------- colors

  _extent(facet) {
    if (!this._extents.has(facet.id)) {
      this._extents.set(facet.id, extent(facet, this.items));
    }
    return this._extents.get(facet.id);
  }

  /** Stable value->color map for a categorical facet (fixed, never re-ranked). */
  _categories(facet) {
    if (!this._categoryColors.has(facet.id)) {
      this._categoryColors.set(facet.id, facet.categoryColors(this.items));
    }
    return this._categoryColors.get(facet.id);
  }

  _legendFacet(id) {
    const f = this.byId.get(id);
    return f.type === "string" || f.type === "boolean"
      ? { ...f, categoryColors: () => this._categories(f) }
      : f;
  }

  _colorsFor(visible) {
    const s = this.state;
    const out = new Map();
    const active = s.colorIds.map(id => this.byId.get(id));
    for (const item of visible) {
      const id = this.opts.idOf(item);
      if (!active.length) { out.set(id, this.opts.baseColor(item)); continue; }
      const parts = [];
      for (const f of active) {
        const v = facetValue(f, item);
        if (v === null) { parts.push(MISSING_COLOR); continue; }
        if (f.type === "string" || f.type === "boolean") {
          parts.push(this._categories(f).get(v) ?? MISSING_COLOR);
        } else {
          const ext = this._extent(f);
          parts.push(f.colorRamp(ext ? normalize(f, v, ext) : 0.5));
        }
      }
      out.set(id, blendRgb(parts));
    }
    return out;
  }

  // ------------------------------------------------------------ pointer

  _bindPointer() {
    const c = this.ui.canvas;
    let down = null, dragged = false;
    c.addEventListener("pointerdown", e => {
      down = { x: e.clientX, y: e.clientY };
      dragged = false;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", e => {
      if (down) {
        const dx = e.clientX - down.x, dy = e.clientY - down.y;
        if (dragged || Math.hypot(dx, dy) > 4) {
          dragged = true;
          this.renderer.panBy(dx, dy);
          down = { x: e.clientX, y: e.clientY };
        }
        return;
      }
      const id = this._pick(e);
      this.state.hoverId = id;
      c.style.cursor = id !== null ? "pointer" : "default";
      const item = id !== null ? this.itemById.get(id) : null;
      this.ui.showTooltip(item ? this.opts.tooltipText(item) : null, e.clientX, e.clientY);
    });
    const up = e => {
      if (down && !dragged) {
        const id = this._pick(e);
        this.state.selectId = id;
        const sel = id !== null ? this.itemById.get(id) : null;
        this.ui.renderDetail(sel, sel ? this.opts.detailRows(sel) : null);
      }
      down = null;
    };
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", () => { down = null; });
    c.addEventListener("wheel", e => {
      e.preventDefault();
      this.renderer.zoomBy(Math.exp(e.deltaY * 0.0016), e.clientX, e.clientY);
    }, { passive: false });
    c.addEventListener("pointerleave", () => this.ui.showTooltip(null));
  }

  _pick(e) {
    if (this.state.view === "orbit") {
      return this.renderer.pickScreen(e.clientX, e.clientY, this.anim.items);
    }
    const w = this.renderer.unproject(e.clientX, e.clientY);
    return this.layout ? hitTest(this.layout.positions, w.x, w.y) : null;
  }

  // ---------------------------------------------------------------- loop

  _loop() {
    const now = performance.now();
    this.anim.tick(now);
    if (this._orbitSetup) {
      this.opts.orbitView.tick(now, this.anim, this.state.pulseId,
        this.state.view === "orbit", this.renderer.camera.position,
        this.state.selectId);
    }
    this.renderer.draw(this.anim.items, {
      hoverId: this.state.hoverId,
      selectId: this.state.selectId,
      pulseId: this.state.pulseId,
      now,
    });
    if (this.layout) {
      this.ui.updateLabels(this.layout.labels,
        (x, y) => this.renderer.project(x, y));
    }
    requestAnimationFrame(this._loop);
  }

  // ------------------------------------------------------------ UI wires

  _callbacks() {
    const s = () => this.state;
    return {
      onView: v => { s().view = v; this.update(); },
      onSort: id => { s().sortId = id; this.update(); },
      onSortDir: () => { s().sortDir *= -1; this.update(); },
      onBucket: id => { s().bucketId = id; this.update(); },
      onColorToggle: id => {
        const ids = s().colorIds;
        s().colorIds = ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id];
        this.update();
      },
      onColorStops: (id, stops) => this.setColorStops(id, stops),
      onScale: (id, scale) => this.setScale(id, scale),
      onSearch: text => {
        s().filters = setFilter(s().filters, "search", text ? { text } : null);
        this.update();
      },
      onStringToggle: (id, v) => {
        s().filters = toggleStringValue(s().filters, id, v);
        this.update();
      },
      onBoolFilter: (id, v) => {
        s().filters = setFilter(s().filters, id, v === null ? null : { value: v });
        this.update();
      },
      onAgoFilter: (id, ms) => {
        s().filters = setFilter(s().filters, id, ms === null ? null : { withinMs: ms });
        this.update();
      },
      onRangeFilter: (id, range) => {
        s().filters = setFilter(s().filters, id, range);
        this.update();
      },
      onClearFilter: id => {
        s().filters = setFilter(s().filters, id, null);
        this.update();
      },
      onClearAll: () => { s().filters = createFilters(); this.update(); },
    };
  }

  /** App API: mark one item as "live" (pulsing ring), e.g. last connected. */
  setPulse(id) { this.state.pulseId = id; }

  /** App API: re-run the pipeline after external item mutations.
   *  Color extents are recomputed — live data can grow past cached ranges. */
  refresh() {
    this._extents.clear();
    this.update();
  }

  /** Switch a numeric facet between linear and log scale (color editor
   *  checkbox) — affects color, filter histogram and graph bucketing. */
  setScale(facetId, scale) {
    const f = this.byId.get(facetId);
    f.scale = scale === "log" ? "log" : undefined;
    this._extents.clear();
    this.opts.onScale?.(facetId, scale);
    this.update();
  }

  /** Replace a facet's gradient endpoints (from the color editor). */
  setColorStops(facetId, stops) {
    const f = this.byId.get(facetId);
    f.colorStops = stops;
    f.colorRamp = makeTwoColorRamp(stops[0], stops[1]);
    this._categoryColors.delete(facetId);
    this.ui.updateChipSwatch(f);
    this.opts.onColorStops?.(facetId, stops);
    this.update();
  }
}
