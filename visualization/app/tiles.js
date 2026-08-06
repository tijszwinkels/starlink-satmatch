// Zoom-in ground detail: NASA GIBS "Black Marble" night tiles (Web
// Mercator), composed onto a canvas and draped over the visible patch of
// the globe. The patch mesh is linear in longitude and in Mercator-y, so
// the tile raster maps onto it without resampling. Rebuilds (debounced)
// as the camera moves; tiles fade in as they arrive.

import * as THREE from "../lib/vendor/three.module.js";

const URL_OF = (z, x, y) =>
  `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/` +
  `default/default/GoogleMapsCompatible_Level8/${z}/${y}/${x}.png`;
export const ATTRIBUTION = "ground imagery: NASA GIBS · VIIRS Black Marble";

const MIN_Z = 3, MAX_Z = 8;      // the GIBS layer tops out at level 8
const GRID = 5;                  // tiles per side
const TILE_PX = 256;
const SHOW_BELOW_RADIUS = 2600;  // world units; camera closer -> tiles on
const SEG = 32;                  // patch tessellation
const DEBOUNCE_MS = 400;
const TAN_HALF_FOV = Math.tan(15 * Math.PI / 180);

const lon2tile = (lon, z) => (lon + 180) / 360 * 2 ** z;
const lat2tile = (lat, z) => (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * 2 ** z;
const tile2lon = (x, z) => x / 2 ** z * 360 - 180;
const tile2lat = (y, z) =>
  Math.atan(Math.sinh(Math.PI * (1 - 2 * y / 2 ** z))) * 180 / Math.PI;

export function createTileOverlay(parent, { radiusWorld, worldPerKm }) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = GRID * TILE_PX;
  const ctx = canvas.getContext("2d");
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;

  const material = new THREE.MeshBasicMaterial({
    map: texture, transparent: true, depthWrite: false,
    side: THREE.DoubleSide,
  });
  let mesh = null;
  let currentKey = null, lastBuild = 0, generation = 0;

  // lat/lon (degrees) -> world point just above the globe surface
  function surfWorld(lat, lon, lift = 1.004) {
    const φ = lat * Math.PI / 180, λ = lon * Math.PI / 180;
    const r = radiusWorld * lift;
    return [
      r * Math.cos(φ) * Math.cos(λ),
      r * Math.sin(φ),
      -r * Math.cos(φ) * Math.sin(λ),
    ];
  }

  function buildPatch(z, x0, y0) {
    const positions = [], uvs = [], indices = [];
    for (let j = 0; j <= SEG; j++) {
      const tileY = y0 + j / SEG * GRID;           // linear in Mercator-y
      const lat = tile2lat(tileY, z);
      for (let i = 0; i <= SEG; i++) {
        const lon = tile2lon(x0 + i / SEG * GRID, z);
        positions.push(...surfWorld(lat, lon));
        uvs.push(i / SEG, 1 - j / SEG);
      }
    }
    for (let j = 0; j < SEG; j++) {
      for (let i = 0; i < SEG; i++) {
        const a = j * (SEG + 1) + i;
        indices.push(a, a + 1, a + SEG + 1, a + 1, a + SEG + 2, a + SEG + 1);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    geo.setIndex(indices);
    if (mesh) {
      mesh.geometry.dispose();
      mesh.geometry = geo;
    } else {
      mesh = new THREE.Mesh(geo, material);
      // in the transparent pass: after the atmosphere rim (-2), but BEFORE
      // the satellite sprites (0) — sprites must paint over the ground
      mesh.renderOrder = -1;
      parent.add(mesh);
    }
  }

  function fetchTiles(z, x0, y0) {
    const gen = ++generation;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    texture.needsUpdate = true;
    const n = 2 ** z;
    for (let j = 0; j < GRID; j++) {
      for (let i = 0; i < GRID; i++) {
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
          if (gen !== generation) return;
          ctx.drawImage(img, i * TILE_PX, j * TILE_PX);
          texture.needsUpdate = true;
        };
        img.src = URL_OF(z, ((x0 + i) % n + n) % n, y0 + j);
      }
    }
  }

  return {
    /** Call per frame with the orbit camera position; returns visibility. */
    update(camPos, nowMs) {
      const radius = Math.hypot(camPos.x, camPos.y, camPos.z);
      if (radius > SHOW_BELOW_RADIUS) {
        if (mesh) mesh.visible = false;
        return false;
      }
      const lat = Math.asin(camPos.y / radius) * 180 / Math.PI;
      const lon = Math.atan2(-camPos.z, camPos.x) * 180 / Math.PI;
      // pick z so one tile pixel ~ one screen pixel (floor: prefer full
      // coverage over oversharp tiles that wouldn't span the view)
      const viewPx = document.querySelector(".mm-canvas")?.clientHeight ?? 900;
      const heightKm = 2 * (radius - radiusWorld) / worldPerKm * TAN_HALF_FOV;
      const ringKm = 40075 * Math.cos(lat * Math.PI / 180);
      const z = Math.max(MIN_Z, Math.min(MAX_Z,
        Math.floor(Math.log2(ringKm * viewPx / (TILE_PX * heightKm)))));
      const x0 = Math.floor(lon2tile(lon, z)) - (GRID >> 1);
      const y0 = Math.max(0, Math.min(2 ** z - GRID,
        Math.floor(lat2tile(lat, z)) - (GRID >> 1)));
      const key = `${z}/${x0}/${y0}`;
      if (key !== currentKey && nowMs - lastBuild > DEBOUNCE_MS) {
        currentKey = key;
        lastBuild = nowMs;
        buildPatch(z, x0, y0);
        fetchTiles(z, x0, y0);
      }
      if (mesh) mesh.visible = true;
      return true;
    },
    material,          // so the globe fade can include the patch
  };
}
