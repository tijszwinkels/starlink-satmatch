// Layout engines. Pure: items in, world-space rectangles out.
// World space: y up, layout fits a box of height WORLD_H and width
// WORLD_H * aspect, centered on the origin. The renderer fits its camera to
// the returned bounds, so world scale is arbitrary but consistent.

export const WORLD_H = 1000;

/**
 * Grid view: near-square cell grid, row-major (top-left first), which makes
 * the sort order read like text.
 * Returns { positions: Map(id -> {x, y, size}), bounds, labels: [] }
 */
export function gridLayout(ids, { aspect = 1.6, gap = 0.1 } = {}) {
  const n = ids.length;
  if (!n) return empty();
  const cols = Math.max(1, Math.round(Math.sqrt(n * aspect)));
  const rows = Math.ceil(n / cols);
  const cell = WORLD_H / Math.max(rows, cols / aspect);
  const w = cols * cell, h = rows * cell;
  const positions = new Map();
  ids.forEach((id, i) => {
    const col = i % cols, row = (i / cols) | 0;
    positions.set(id, {
      x: -w / 2 + (col + 0.5) * cell,
      y: h / 2 - (row + 0.5) * cell,
      size: cell * (1 - gap),
    });
  });
  return { positions, bounds: bounds(w, h), labels: [], columns: [] };
}

/**
 * Graph (histogram) view: one column per group, items stacked bottom-up in
 * sub-columns of k tiles across — the bar IS the items (Pivot's graph view).
 * groups: [{ label, ids }] in bucket order.
 * Returns { positions, bounds, labels: [{text, x, y, count}], columns: [...] }
 */
export function histogramLayout(groups, { aspect = 1.6, gap = 0.12 } = {}) {
  const m = groups.length;
  if (!m || !groups.some(g => g.ids.length)) return empty();
  const w = WORLD_H * aspect;
  const labelBand = WORLD_H * 0.06;           // reserved strip under the bars
  const h = WORLD_H - labelBand;
  const colW = w / m;
  const maxCount = Math.max(...groups.map(g => g.ids.length));

  // smallest k (tiles across per column) whose stacks fit the height
  let k = 1, tile;
  for (; ; k++) {
    tile = (colW * (1 - gap)) / k;
    if (Math.ceil(maxCount / k) * tile <= h || k > 200) break;
  }

  const positions = new Map();
  const labels = [], columns = [];
  groups.forEach((g, ci) => {
    const x0 = -w / 2 + ci * colW + (colW - k * tile) / 2;
    const yBase = -WORLD_H / 2 + labelBand;
    g.ids.forEach((id, i) => {
      const col = i % k, row = (i / k) | 0;
      positions.set(id, {
        x: x0 + (col + 0.5) * tile,
        y: yBase + (row + 0.5) * tile,
        size: tile * 0.92,
      });
    });
    labels.push({
      text: g.label, count: g.ids.length,
      x: -w / 2 + (ci + 0.5) * colW, y: -WORLD_H / 2 + labelBand * 0.45,
    });
    columns.push({
      x: -w / 2 + ci * colW, width: colW,
      y: -WORLD_H / 2, height: WORLD_H, shaded: ci % 2 === 1,
    });
  });
  return { positions, bounds: bounds(w, WORLD_H), labels, columns };
}

function bounds(w, h) {
  return { minX: -w / 2, maxX: w / 2, minY: -h / 2, maxY: h / 2 };
}

function empty() {
  return { positions: new Map(), bounds: bounds(WORLD_H, WORLD_H), labels: [], columns: [] };
}

/** Spatial hit-test over a layout: returns id at world point, or null. */
export function hitTest(positions, wx, wy) {
  let best = null, bestD = Infinity;
  for (const [id, p] of positions) {
    const half = p.size / 2;
    if (Math.abs(wx - p.x) <= half && Math.abs(wy - p.y) <= half) {
      const d = (wx - p.x) ** 2 + (wy - p.y) ** 2;
      if (d < bestD) { best = id; bestD = d; }
    }
  }
  return best;
}
