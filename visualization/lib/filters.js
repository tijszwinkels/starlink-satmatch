// Filter state and predicates. State is a plain Map facetId -> descriptor:
//   string:   { values: Set }           — item value must be in the set
//   number:   { min, max }              — inclusive range (either side null = open)
//   datetime: { withinMs } | { min, max } — "how long ago" or absolute range
//   boolean:  { value: true|false }
// plus a special "search" entry: { text } matched by the app-supplied searcher.

import { facetValue } from "./facets.js";

export function createFilters() {
  return new Map();
}

export function setFilter(filters, facetId, descriptor) {
  const next = new Map(filters);
  if (descriptor === null) next.delete(facetId);
  else next.set(facetId, descriptor);
  return next;
}

export function toggleStringValue(filters, facetId, value) {
  const cur = filters.get(facetId)?.values ?? new Set();
  const values = new Set(cur);
  values.has(value) ? values.delete(value) : values.add(value);
  return setFilter(filters, facetId, values.size ? { values } : null);
}

function matches(facet, desc, item, now) {
  const v = facetValue(facet, item);
  if (desc.values) return desc.values.has(v);
  if (desc.value !== undefined) return v === desc.value;
  if (desc.withinMs !== undefined)
    return v !== null && now - v <= desc.withinMs;
  if (v === null) return false;
  if (desc.min !== null && desc.min !== undefined && v < desc.min) return false;
  if (desc.max !== null && desc.max !== undefined && v > desc.max) return false;
  return true;
}

/**
 * Predicate over all filters. `skipFacetId` supports faceted-search counts
 * (each facet's own option counts are computed against the *other* filters).
 */
export function predicate(facetsById, filters, { now = Date.now(), skipFacetId, search } = {}) {
  const active = [...filters].filter(([id]) => id !== skipFacetId);
  return item => {
    for (const [id, desc] of active) {
      if (id === "search") {
        if (search && !search(item, desc.text)) return false;
        continue;
      }
      if (!matches(facetsById.get(id), desc, item, now)) return false;
    }
    return true;
  };
}

/** Per-value counts for a string/boolean facet, respecting other filters. */
export function optionCounts(facet, items, otherPred) {
  const counts = new Map();
  for (const item of items) {
    if (!otherPred(item)) continue;
    const v = facetValue(facet, item);
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  return counts;
}

/** Human-readable chips for the applied-filters breadcrumb. */
export function filterChips(facetsById, filters, formatFn) {
  const chips = [];
  for (const [id, desc] of filters) {
    if (id === "search") { chips.push({ facetId: id, label: `"${desc.text}"` }); continue; }
    const facet = facetsById.get(id);
    let label;
    if (desc.values) label = [...desc.values].map(v => formatFn(facet, v)).join(", ");
    else if (desc.value !== undefined) label = formatFn(facet, desc.value);
    else if (desc.withinMs !== undefined) label = `≤ ${humanDuration(desc.withinMs)} ago`;
    else label = `${desc.min !== null ? formatFn(facet, desc.min) : ""}–${desc.max !== null ? formatFn(facet, desc.max) : ""}`;
    chips.push({ facetId: id, label: `${facet.label}: ${label}` });
  }
  return chips;
}

export function humanDuration(ms) {
  const h = ms / 3600e3;
  if (h < 1) return `${Math.round(ms / 60e3)} min`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} d`;
}
