# murmuration — Pivot-style satellite visualization

A faceted visualization of the satmatch satellite history in the spirit of
[Microsoft Live Labs Pivot](https://en.wikipedia.org/wiki/Microsoft_Live_Labs_Pivot)
(2009): every satellite is an icon; filtering, sorting, bucketing and
re-coloring make the whole flock *fly* to its new arrangement rather than
jump-cut. (A murmuration is a flock of starlings moving as one.)

## Run

```sh
python3 visualization/export.py        # merge catalogue + history -> satellites.json
cd visualization && python3 -m http.server 8642
# open http://localhost:8642
```

`export.py` reuses the satmatch modules: TLE catalogue (orbit, raw TLE),
SATCAT (launch date/site, status), `sat_history.json` (your dwells, slots,
tracked time, transfer volumes, last-seen) and the hardware-type heuristic.
Re-run it any time to refresh; `satellites.json` is gitignored (personal data).

## What you can do

- **Grid view** — all 10k+ satellites sorted by any facet.
- **Graph view** — a histogram literally stacked out of the satellite icons,
  bucketed by any facet (type, launch quarter, inclination, altitude, dwells,
  transfer volume, last-connected …). Buckets show label + count.
- **Filters** (left pane) — checkbox facets with live counts, numeric range
  sliders (log-scaled for bytes), "how long ago" presets for datetimes, name
  search. Active filters become removable breadcrumb chips.
- **Color** (top bar) — toggle any combination of gradients; multiple active
  facets blend per-satellite in OKLab. Each gradient runs between two editable
  endpoint colors: **double-click a chip** to change them (persisted in
  localStorage). Default coloring: bright = connected, dim = never connected,
  green pulse = the satellite you're on right now.
- **Live current satellite** — while `satmatch.py identify --dwells` runs it
  publishes `visualization/current.json` on every confident observation; the
  page polls it every 5 s, moves the green pulse on handovers and shows
  `● live: <name>` in the status bar (falls back to the export's
  last-connected when the file is absent or stale).
- **Icons** — Gen1 (single solar sail, vertical) vs V2 Mini (two wings,
  horizontal); click for the detail pane, hover for a tooltip; wheel to zoom,
  drag to pan.

## The animation

The signature Pivot choreography, reconstructed from the era's sources (see
`notes/reference/pivot-interface-research.md` in the parent project):
filtered-out items fly **outward along the ray from the layout center through
their current position** (~0.9 s), then after a beat (~0.45 s) the survivors
glide to their new spots (~0.9 s, circular ease-in-out); newly matching items
fly in along their own rays. A pure re-sort skips the stagger. As Gary Flake
put it: *"if you make it a sudden transition, people lose their way … smooth
and continuous [gives] people a mental model of how they got to where they
are."*

## Library layout

`lib/` is a generic, dependency-light library (three.js vendored, no build
step); `app/` is the Starlink-specific configuration.

| file | role |
|---|---|
| `lib/murmuration.js` | orchestrator + public API |
| `lib/facets.js` | facet model: one definition serves filter/sort/bucket/color |
| `lib/filters.js` | filter state, predicates, faceted counts, chips |
| `lib/layouts.js` | grid + histogram layouts (pure), hit-testing |
| `lib/animate.js` | tween engine + the exit/stagger/settle choreography |
| `lib/color.js` | OKLab ramps, gradient blending |
| `lib/renderer.js` | three.js instanced rendering, ortho camera, pan/zoom |
| `lib/ui.js` | DOM chrome: panes, chips, legend, labels, tooltip |
| `app/main.js` | facet definitions for the satellite dataset |
| `app/icons.js` | canvas-drawn white silhouettes (tinted per instance) |

Facets are plain objects: `{id, label, type: string|number|datetime|boolean,
get(item), format?, scale?: 'log', filterable?, sortable?, bucketable?,
colorable?, colorRamp?, invertColor?, agoPresets?, categoryColors?}` — see
`app/main.js` for the full worked example.

Tests: `node --test test/` (pure modules only).

## Not yet built

- Orbital view (satellites circling a globe in the same instanced canvas,
  flying into the data views on switch) — the renderer was chosen for this.
- Semantic-zoom cards (full metadata card when zoomed close, Pivot-style).
- Live refresh of the *history* facets (dwells/slots/bytes still need a
  re-export; only the current satellite updates live).
