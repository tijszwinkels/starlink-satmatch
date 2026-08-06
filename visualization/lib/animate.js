// The Pivot choreography. On every re-layout items split three ways:
//   leavers  — fly OUT along the ray from layout center through their current
//              position (the radial "explosion"), then despawn
//   stayers  — after a stagger beat, glide to their new position
//   enterers — fly IN along their own ray from outside, with the stayers
// Timing follows the original: exit and move ~1s, stagger ~0.5s, stagger
// skipped when nothing leaves (pure re-sort). Easing: circular in-out.
//
// The Animator owns per-item animated state {x, y, size, r, g, b}; the
// renderer reads it every frame. Time is injected for testability.

export const easeCircInOut = t =>
  t < 0.5
    ? (1 - Math.sqrt(1 - (2 * t) ** 2)) / 2
    : (Math.sqrt(1 - (2 * t - 2) ** 2) + 1) / 2;

export const easeCubicOut = t => 1 - (1 - t) ** 3;

export const TIMING = { exitMs: 900, moveMs: 900, staggerMs: 450, colorMs: 500 };

export class Animator {
  constructor() {
    this.items = new Map();   // id -> {x,y,size,r,g,b, tweens:{}}
    this.epoch = 0;           // bumped per choreography; stale tweens ignored
  }

  state(id) { return this.items.get(id); }

  /** Ensure an item exists, spawning it at the given state if new. */
  spawn(id, init) {
    let s = this.items.get(id);
    if (!s) { s = { ...init, tweens: {} }; this.items.set(id, s); }
    return s;
  }

  tween(id, props, { delay = 0, duration, ease = easeCircInOut, now, onDone } = {}) {
    const s = this.items.get(id);
    if (!s) return;
    for (const [key, to] of Object.entries(props)) {
      s.tweens[key] = { from: s[key], to, start: now + delay, duration, ease, epoch: this.epoch, onDone };
    }
  }

  /** Advance all tweens; returns true while anything is still animating. */
  tick(now) {
    let active = false;
    for (const [id, s] of this.items) {
      for (const [key, tw] of Object.entries(s.tweens)) {
        if (tw.epoch !== this.epoch) { delete s.tweens[key]; continue; }
        const t = (now - tw.start) / tw.duration;
        if (t < 0) { active = true; continue; }
        if (t >= 1) {
          s[key] = tw.to;
          delete s.tweens[key];
          tw.onDone?.(id, key);
        } else {
          s[key] = tw.from + (tw.to - tw.from) * tw.ease(t);
          active = true;
        }
      }
    }
    return active;
  }

  despawn(id) { this.items.delete(id); }
}

/** Point on the ray center->pos, pushed out to `radius` from center.
 *  An item exactly at the center has no ray; it leaves diagonally. */
export function radialExitPoint(pos, radius) {
  let { x, y } = pos;
  if (Math.hypot(x, y) < 1e-6) { x = 0.7071; y = 0.7071; }
  const d = Math.hypot(x, y);
  // never closer than the item already is
  const r = Math.max(radius, d * 1.2);
  return { x: x / d * r, y: y / d * r };
}

/**
 * Plan and start the transition to a new layout.
 * prevVisible: Set of ids currently on screen; next: layout positions Map.
 * exitRadius: world distance guaranteed off-screen.
 * Returns sets for the caller (renderer bookkeeping / despawn scheduling).
 */
export function choreograph(anim, prevVisible, nextPositions, {
  now, exitRadius, timing = TIMING, sizeOf = p => p.size,
} = {}) {
  anim.epoch++;
  const leavers = [], stayers = [], enterers = [];
  for (const id of prevVisible) {
    (nextPositions.has(id) ? stayers : leavers).push(id);
  }
  for (const id of nextPositions.keys()) {
    if (!prevVisible.has(id)) enterers.push(id);
  }

  const hasLeavers = leavers.length > 0;
  const delay = hasLeavers ? timing.staggerMs : 0;

  for (const id of leavers) {
    const s = anim.state(id);
    if (!s) continue;
    const out = radialExitPoint(s, exitRadius);
    anim.tween(id, { x: out.x, y: out.y }, {
      now, duration: timing.exitMs,
      onDone: (itemId, key) => { if (key === "x") anim.despawn(itemId); },
    });
  }
  for (const id of stayers) {
    const p = nextPositions.get(id);
    anim.tween(id, { x: p.x, y: p.y, size: sizeOf(p) },
      { now, delay, duration: timing.moveMs });
  }
  for (const id of enterers) {
    const p = nextPositions.get(id);
    const from = radialExitPoint(p, exitRadius);
    anim.spawn(id, { x: from.x, y: from.y, size: sizeOf(p), r: 1, g: 1, b: 1 });
    anim.tween(id, { x: p.x, y: p.y, size: sizeOf(p) },
      { now, delay, duration: timing.moveMs });
  }
  return { leavers, stayers, enterers };
}

/** Tween every visible item toward its target color. */
export function recolor(anim, colors, { now, timing = TIMING } = {}) {
  for (const [id, [r, g, b]] of colors) {
    if (anim.state(id)) anim.tween(id, { r, g, b }, { now, duration: timing.colorMs, ease: easeCubicOut });
  }
}
