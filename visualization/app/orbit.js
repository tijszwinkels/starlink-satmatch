// Orbital view: real satellite positions from the exported TLEs (SGP4 via
// satellite.js), an Earth-fixed frame (night-lights globe, dish at a fixed
// point), and a line from the dish to the currently-serving satellite.
//
// Frame mapping: ECEF (x: lon 0 equator, y: lon 90E, z: north pole) to the
// renderer's y-up world as (x, y, z)_world = (ecf.x, ecf.z, -ecf.y) * KM.
// Between full SGP4 refreshes positions extrapolate linearly in ECI — a few
// km of error at most, invisible at globe scale.

import * as THREE from "../lib/vendor/three.module.js";
import * as satellite from "../lib/vendor/satellite.es.js";
import { createTileOverlay, ATTRIBUTION } from "./tiles.js";

const KM = 0.1;                    // world units per km
const R_EARTH = 6371 * KM;
const SAT_SIZE = 10;               // sprite size in world units
const REPROP_CHUNK = 700;          // satrecs re-propagated per frame
const REPROP_MS = 45000;           // full refresh cadence

const toWorld = ecf => ({ x: ecf.x * KM, y: ecf.z * KM, z: -ecf.y * KM });

export function createOrbitView(items, observer) {
  const sats = new Map();          // norad -> {satrec, eciP, eciV, epochMs}
  let inited = false;
  let group = null, lineMesh = null, dishMesh = null;
  let fadeMats = [];               // [{mat, base}] scaled together by fade
  let fade = null;                 // {from, to, start} for globe opacity
  let repropQueue = [];
  let tiles = null, attributionEl = null;

  // lifted just above the tile-patch shell so it never gets draped over
  const dishWorld = observer ? (() => {
    const e = toWorld(satellite.geodeticToEcf({
      latitude: observer.lat * Math.PI / 180,
      longitude: observer.lon * Math.PI / 180,
      height: (observer.alt_m ?? 0) / 1000,
    }));
    const m = Math.hypot(e.x, e.y, e.z);
    const r = R_EARTH * 1.008;
    return { x: e.x / m * r, y: e.y / m * r, z: e.z / m * r };
  })() : null;

  function propagateOne(entry, date) {
    const pv = satellite.propagate(entry.satrec, date);
    if (!pv?.position) return false;
    entry.eciP = pv.position;
    entry.eciV = pv.velocity;
    entry.epochMs = date.getTime();
    return true;
  }

  function init() {
    const t0 = performance.now();
    const date = new Date();
    for (const it of items) {
      if (!it.tle) continue;
      const entry = { satrec: satellite.twoline2satrec(it.tle[0], it.tle[1]) };
      if (propagateOne(entry, date)) sats.set(it.norad, entry);
    }
    inited = true;
    console.log(`orbit: ${sats.size} satrecs initialized in ${Math.round(performance.now() - t0)} ms`);
  }

  function worldAt(entry, nowMs, gmst) {
    const dt = (nowMs - entry.epochMs) / 1000;
    const eci = {
      x: entry.eciP.x + entry.eciV.x * dt,
      y: entry.eciP.y + entry.eciV.y * dt,
      z: entry.eciP.z + entry.eciV.z * dt,
    };
    return toWorld(satellite.eciToEcf(eci, gmst));
  }

  return {
    /** Layout positions for the murmuration pipeline (wall-clock based). */
    positions(visibleItems) {
      if (!inited) init();
      const t = Date.now();
      const gmst = satellite.gstime(new Date(t));
      const map = new Map();
      for (const it of visibleItems) {
        const entry = sats.get(it.norad);
        if (!entry) continue;
        map.set(it.norad, { ...worldAt(entry, t, gmst), size: SAT_SIZE });
      }
      return map;
    },

    /** Initial camera: hover above the dish (or Greenwich without one). */
    camera() {
      const p = dishWorld ?? { x: R_EARTH, y: 0, z: 0 };
      const r = Math.hypot(p.x, p.y, p.z);
      return {
        theta: Math.atan2(p.z, p.x),
        phi: Math.acos(p.y / r),
        radius: 4200,
      };
    },

    setup(scene) {
      group = new THREE.Group();
      const tex = new THREE.TextureLoader().load("lib/vendor/earth-night.jpg");
      tex.colorSpace = THREE.SRGBColorSpace;
      const globe = new THREE.Mesh(
        new THREE.SphereGeometry(R_EARTH, 64, 32),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
      // three.js sphere UVs already put texture lon 0 at +x for equirect
      // earth maps (verified against reference markers) — no rotation.
      const rim = new THREE.Mesh(
        new THREE.SphereGeometry(R_EARTH * 1.015, 64, 32),
        new THREE.MeshBasicMaterial({
          color: 0x1e3a5f, transparent: true, opacity: 0.35,
          side: THREE.BackSide,
        }));
      // the globe is transparent (for the fade), so the whole stack lives
      // in the transparent pass and needs explicit ordering:
      // rim (-3) -> globe (-2) -> tile patch (-1) -> sprites & rings (0)
      rim.renderOrder = -3;
      globe.renderOrder = -2;
      group.add(globe, rim);

      tiles = createTileOverlay(group, {
        radiusWorld: R_EARTH, worldPerKm: KM,
      });
      fadeMats = [
        { mat: globe.material, base: 1 },
        { mat: rim.material, base: 0.35 },
        { mat: tiles.material, base: 1 },
      ];
      attributionEl = document.createElement("div");
      attributionEl.className = "mm-attribution";
      attributionEl.textContent = ATTRIBUTION;
      attributionEl.style.display = "none";
      document.querySelector(".mm-canvas-wrap")?.append(attributionEl);

      if (dishWorld) {
        dishMesh = new THREE.Mesh(
          new THREE.SphereGeometry(7, 16, 8),
          new THREE.MeshBasicMaterial({ color: 0x0ca30c }));
        dishMesh.position.set(dishWorld.x, dishWorld.y, dishWorld.z);
        group.add(dishMesh);

        const lineGeo = new THREE.BufferGeometry().setFromPoints(
          [new THREE.Vector3(), new THREE.Vector3()]);
        lineMesh = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({
          color: 0x0ca30c, transparent: true, opacity: 0.85,
        }));
        lineMesh.visible = false;
        group.add(lineMesh);
      }
      group.visible = false;
      scene.add(group);
    },

    enter() {
      group.visible = true;
      fade = { from: 0, to: 1, start: performance.now() };
    },
    leave() {
      fade = { from: 1, to: 0, start: performance.now() };
    },

    /** Per-frame: fade globe; when active, drift satellites and track line. */
    tick(nowMs, anim, currentId, active, camPos) {
      if (!inited || !group) return;

      if (fade) {
        const t = Math.min(1, (nowMs - fade.start) / 700);
        const o = fade.from + (fade.to - fade.from) * t;
        for (const { mat, base } of fadeMats) mat.opacity = o * base;
        if (t >= 1) {
          if (fade.to === 0) group.visible = false;
          fade = null;
        }
      }
      if (!active || !camPos) {
        if (lineMesh) lineMesh.visible = false;
        if (attributionEl) attributionEl.style.display = "none";
        return;
      }

      // the ground dot keeps a roughly constant apparent size
      if (dishMesh) {
        const d = Math.hypot(camPos.x - dishWorld.x, camPos.y - dishWorld.y,
          camPos.z - dishWorld.z);
        dishMesh.scale.setScalar(Math.min(1, Math.max(0.1, d / 3500)));
      }

      const showTiles = tiles.update(camPos, Date.now());
      if (attributionEl) {
        attributionEl.style.display = showTiles ? "block" : "none";
      }

      // rolling SGP4 refresh, a chunk per frame
      const t = Date.now();
      if (!repropQueue.length &&
          [...sats.values()].some(e => t - e.epochMs > REPROP_MS)) {
        repropQueue = [...sats.values()];
      }
      if (repropQueue.length) {
        const date = new Date();
        for (const entry of repropQueue.splice(0, REPROP_CHUNK)) {
          propagateOne(entry, date);
        }
      }

      // real-time drift for settled items (tweens own the transitions)
      const gmst = satellite.gstime(new Date(t));
      for (const [id, s] of anim.items) {
        if (s.tweens.x) continue;
        const entry = sats.get(id);
        if (!entry) continue;
        const w = worldAt(entry, t, gmst);
        s.x = w.x; s.y = w.y; s.z = w.z;
      }

      if (lineMesh) {
        const s = currentId !== null ? anim.state(currentId) : null;
        if (s && !s.tweens.x) {
          const pos = lineMesh.geometry.attributes.position;
          pos.setXYZ(0, dishWorld.x, dishWorld.y, dishWorld.z);
          pos.setXYZ(1, s.x, s.y, s.z);
          pos.needsUpdate = true;
          lineMesh.visible = true;
        } else {
          lineMesh.visible = false;
        }
      }
    },
  };
}
