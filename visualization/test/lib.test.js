// Unit tests for the pure murmuration modules (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";

import { hexToRgb, rgbToHex, rgbToOklab, oklabToRgb, makeRamp, makeTwoColorRamp, blendRgb } from "../lib/color.js";
import { facetValue, formatValue, extent, normalize, niceStep, makeBuckets, groupByBuckets, compareBy } from "../lib/facets.js";
import { createFilters, setFilter, toggleStringValue, predicate, optionCounts, filterChips } from "../lib/filters.js";
import { gridLayout, histogramLayout, hitTest, WORLD_H } from "../lib/layouts.js";
import { Animator, choreograph, radialExitPoint, easeCircInOut, TIMING } from "../lib/animate.js";

// ------------------------------------------------------------------ color

test("oklab roundtrip", () => {
  for (const hex of ["#3987e5", "#d95926", "#0ca30c", "#ffffff", "#111111"]) {
    assert.equal(rgbToHex(oklabToRgb(rgbToOklab(hexToRgb(hex)))), hex);
  }
});

test("ramp is monotonic in lightness and stays in hue family", () => {
  const ramp = makeRamp("#3987e5");
  const L = t => rgbToOklab(ramp(t))[0];
  assert.ok(L(0) < L(0.5) && L(0.5) < L(1));
  const [r, g, b] = ramp(1);
  assert.ok(b > r, "blue ramp should stay blue");
});

test("blend of a color with itself is itself", () => {
  const c = hexToRgb("#d95926");
  const blended = blendRgb([c, c]);
  assert.equal(rgbToHex(blended), "#d95926");
});

test("blend of two colors lies between them", () => {
  const blended = blendRgb([hexToRgb("#000000"), hexToRgb("#ffffff")]);
  const L = rgbToOklab(blended)[0];
  assert.ok(L > 0.3 && L < 0.7);
});

test("two-color ramp hits both endpoints and brightens between", () => {
  const ramp = makeTwoColorRamp("#0d366b", "#55d6f5");
  assert.equal(rgbToHex(ramp(0)), "#0d366b");
  assert.equal(rgbToHex(ramp(1)), "#55d6f5");
  const midL = rgbToOklab(ramp(0.5))[0];
  assert.ok(midL > rgbToOklab(ramp(0))[0] && midL < rgbToOklab(ramp(1))[0]);
});

// ----------------------------------------------------------------- facets

const numFacet = { id: "n", label: "n", type: "number", get: x => x.n };
const logFacet = { id: "b", label: "b", type: "number", scale: "log", get: x => x.b };
const strFacet = { id: "s", label: "s", type: "string", get: x => x.s };
const dateFacet = { id: "d", label: "d", type: "datetime", get: x => x.d };

test("extent skips missing values", () => {
  assert.deepEqual(extent(numFacet, [{ n: 3 }, { n: null }, { n: 9 }]), [3, 9]);
  assert.equal(extent(numFacet, [{ n: null }]), null);
});

test("log extent anchors at the smallest positive value", () => {
  assert.deepEqual(extent(logFacet, [{ b: 0 }, { b: 1e5 }, { b: 1e9 }]), [1e5, 1e9]);
  // ramp spread: with the positive anchor, 1 MB sits low, 100 MB high
  const ext = [1e5, 341e6];
  assert.ok(normalize(logFacet, 1e6, ext) < 0.35);
  assert.ok(normalize(logFacet, 1e8, ext) > 0.8);
});

test("log buckets give zeros their own bucket", () => {
  const items = [{ b: 0 }, { b: 0 }, { b: 5e5 }, { b: 2e6 }];
  const buckets = makeBuckets(logFacet, items);
  assert.equal(buckets[0].label, "0");
  const groups = groupByBuckets(logFacet, buckets, items);
  const total = groups.reduce((a, g) => a + g.items.length, 0);
  assert.equal(total, 4, "no satellite falls out of the graph");
  assert.equal(groups[0].items.length, 2);
});

test("normalize linear, log, invert", () => {
  assert.equal(normalize(numFacet, 5, [0, 10]), 0.5);
  assert.equal(normalize({ ...numFacet, invertColor: true }, 10, [0, 10]), 0);
  const t = normalize(logFacet, 1e6, [0, 1e9]);
  assert.ok(t > 0.6 && t < 0.75, `log midpoint ~2/3, got ${t}`);
});

test("niceStep picks 1/2/5 steps", () => {
  assert.equal(niceStep(100, 10), 10);
  assert.equal(niceStep(7, 10), 1);
  assert.equal(niceStep(1000, 4), 500);
});

test("number buckets cover extent and catch missing", () => {
  const items = [{ n: 1 }, { n: 55 }, { n: 99 }, { n: null }];
  const buckets = makeBuckets(numFacet, items);
  const groups = groupByBuckets(numFacet, buckets, items);
  const total = groups.reduce((a, g) => a + g.items.length, 0);
  assert.equal(total, 4);
  assert.equal(groups.at(-1).bucket.label, "no data");
});

test("string buckets sorted, datetime buckets by year over long spans", () => {
  const b = makeBuckets(strFacet, [{ s: "b" }, { s: "a" }, { s: "b" }]);
  assert.deepEqual(b.map(x => x.key), ["a", "b"]);
  const y2020 = Date.UTC(2020, 5), y2024 = Date.UTC(2024, 5);
  const db = makeBuckets(dateFacet, [{ d: y2020 }, { d: y2024 }]);
  assert.ok(db.length === 5 && db[0].label === "2020");
});

test("compareBy sorts missing last in both directions", () => {
  const items = [{ n: 2 }, { n: null }, { n: 1 }];
  assert.deepEqual(items.slice().sort(compareBy(numFacet, 1)).map(x => x.n), [1, 2, null]);
  assert.deepEqual(items.slice().sort(compareBy(numFacet, -1)).map(x => x.n), [2, 1, null]);
});

// ---------------------------------------------------------------- filters

test("filters: string toggle, range, ago, counts", () => {
  const byId = new Map([["s", strFacet], ["n", numFacet], ["d", dateFacet]]);
  const items = [
    { s: "a", n: 1, d: Date.now() - 3600e3 },
    { s: "b", n: 5, d: Date.now() - 72 * 3600e3 },
    { s: "b", n: 9, d: null },
  ];
  let f = createFilters();
  f = toggleStringValue(f, "s", "b");
  assert.equal(items.filter(predicate(byId, f)).length, 2);
  f = setFilter(f, "n", { min: 6, max: null });
  assert.equal(items.filter(predicate(byId, f)).length, 1);
  // ago filter: within last 24h
  let g = setFilter(createFilters(), "d", { withinMs: 24 * 3600e3 });
  assert.equal(items.filter(predicate(byId, g)).length, 1);
  // counts for facet s respect OTHER filters only
  const counts = optionCounts(strFacet, items, predicate(byId, f, { skipFacetId: "s" }));
  assert.equal(counts.get("b"), 1);
  assert.equal(filterChips(byId, f, formatValue).length, 2);
});

test("boolean facet value + filter", () => {
  const boolFacet = { id: "e", label: "e", type: "boolean", get: x => x.e };
  const byId = new Map([["e", boolFacet]]);
  const items = [{ e: true }, { e: false }, { e: true }];
  const f = setFilter(createFilters(), "e", { value: true });
  assert.equal(items.filter(predicate(byId, f)).length, 2);
  assert.equal(formatValue(boolFacet, true), "yes");
});

// ---------------------------------------------------------------- layouts

test("grid layout places all ids in bounds without overlap", () => {
  const ids = Array.from({ length: 137 }, (_, i) => i);
  const { positions, bounds } = gridLayout(ids, { aspect: 1.6 });
  assert.equal(positions.size, 137);
  const seen = new Set();
  for (const [, p] of positions) {
    assert.ok(p.x > bounds.minX && p.x < bounds.maxX);
    assert.ok(p.y > bounds.minY && p.y < bounds.maxY);
    const key = `${Math.round(p.x)},${Math.round(p.y)}`;
    assert.ok(!seen.has(key), "no two items share a cell");
    seen.add(key);
  }
});

test("histogram stacks bottom-up and fits the box", () => {
  const groups = [
    { label: "a", ids: [1, 2, 3, 4, 5, 6, 7] },
    { label: "b", ids: [8] },
    { label: "c", ids: [] },
  ];
  const { positions, labels, columns } = histogramLayout(groups, { aspect: 1.6 });
  assert.equal(positions.size, 8);
  // stacking: item 1 is at the bottom of column a
  assert.ok(positions.get(1).y < positions.get(7).y);
  // all items within world box
  for (const [, p] of positions) {
    assert.ok(Math.abs(p.y) <= WORLD_H / 2 && p.size > 0);
  }
  assert.equal(labels.length, 3);
  assert.equal(columns.filter(c => c.shaded).length, 1);
});

test("hitTest finds the tile under the cursor", () => {
  const { positions } = gridLayout([1, 2, 3, 4], { aspect: 1 });
  const p = positions.get(3);
  assert.equal(hitTest(positions, p.x + p.size * 0.3, p.y - p.size * 0.3), 3);
  assert.equal(hitTest(positions, WORLD_H * 5, 0), null);
});

// ---------------------------------------------------------------- animate

test("easing endpoints", () => {
  assert.equal(easeCircInOut(0), 0);
  assert.equal(easeCircInOut(1), 1);
  assert.ok(Math.abs(easeCircInOut(0.5) - 0.5) < 1e-9);
});

test("radial exit point is outside the radius, along the ray", () => {
  const p = radialExitPoint({ x: 30, y: 40 }, 1000);
  assert.ok(Math.hypot(p.x, p.y) >= 1000);
  assert.ok(Math.abs(p.x / p.y - 30 / 40) < 1e-9);
});

test("choreograph: leavers exit+despawn, stayers staggered, enterers spawn", () => {
  const anim = new Animator();
  anim.spawn(1, { x: 0, y: 0, size: 10, r: 1, g: 1, b: 1 });
  anim.spawn(2, { x: 10, y: 0, size: 10, r: 1, g: 1, b: 1 });
  const next = new Map([
    [2, { x: 50, y: 50, size: 8 }],
    [3, { x: 60, y: 60, size: 8 }],
  ]);
  const { leavers, stayers, enterers } = choreograph(
    anim, new Set([1, 2]), next, { now: 0, exitRadius: 500 });
  assert.deepEqual([leavers, stayers, enterers], [[1], [2], [3]]);
  // during the stagger window the stayer has not moved yet
  anim.tick(TIMING.staggerMs - 1);
  assert.equal(anim.state(2).x, 10);
  // leaver in flight
  anim.tick(TIMING.exitMs / 2);
  assert.ok(anim.state(1) && Math.hypot(anim.state(1).x, anim.state(1).y) > 0);
  // after everything: leaver despawned, stayer + enterer arrived
  anim.tick(TIMING.staggerMs + TIMING.moveMs + 1);
  assert.equal(anim.state(1), undefined);
  assert.equal(anim.state(2).x, 50);
  assert.equal(anim.state(3).x, 60);
});

test("re-sort with no leavers has no stagger", () => {
  const anim = new Animator();
  anim.spawn(1, { x: 0, y: 0, size: 10, r: 0, g: 0, b: 0 });
  choreograph(anim, new Set([1]), new Map([[1, { x: 100, y: 0, size: 10 }]]),
    { now: 0, exitRadius: 500 });
  anim.tick(TIMING.moveMs / 2);
  assert.ok(anim.state(1).x > 0, "moves immediately without stagger");
});

test("interrupted transition keeps orphaned leavers animatable", () => {
  const anim = new Animator();
  anim.spawn(1, { x: 5, y: 5, size: 10, r: 0, g: 0, b: 0 });
  // 1 leaves...
  choreograph(anim, new Set([1]), new Map(), { now: 0, exitRadius: 500 });
  anim.tick(TIMING.exitMs / 2);
  const mid = { x: anim.state(1).x, y: anim.state(1).y };
  // ...but a second choreography starts mid-exit and 1 is back in
  choreograph(anim, new Set(anim.items.keys()),
    new Map([[1, { x: 0, y: 0, size: 10 }]]),
    { now: TIMING.exitMs / 2, exitRadius: 500 });
  anim.tick(TIMING.exitMs / 2 + TIMING.moveMs + 1);
  assert.equal(anim.state(1).x, 0, "flies back instead of being stuck");
  assert.ok(Math.hypot(mid.x, mid.y) < 500, "was interrupted mid-flight");
});
