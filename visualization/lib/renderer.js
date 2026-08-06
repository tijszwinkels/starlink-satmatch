// WebGL renderer: every satellite is one instance of an InstancedMesh (one
// mesh per icon variant, so each variant carries its own texture), tinted via
// per-instance color. An orthographic camera pans/zooms over the world plane.
// DOM overlays (labels, panes) live outside; project()/unproject() bridge.

import * as THREE from "./vendor/three.module.js";
import { easeCubicOut, easeCircInOut } from "./animate.js";

const SURFACE = 0x131312;
const SHADE = 0x1d1d1c;

export class Renderer {
  constructor(canvas, { variants }) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(SURFACE);
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -10, 10);
    this.view = { cx: 0, cy: 0, vh: 1200 };       // world center + visible height
    this.camTween = null;
    this.meshes = new Map();                       // variantKey -> InstancedMesh
    this.slots = new Map();                        // id -> {mesh, index}
    this.columnGroup = new THREE.Group();
    this.scene.add(this.columnGroup);
    this._mat4 = new THREE.Matrix4();
    this._color = new THREE.Color();

    for (const v of variants) {
      const tex = new THREE.CanvasTexture(v.image);
      tex.anisotropy = 4;
      const mat = new THREE.MeshBasicMaterial({
        map: tex, transparent: true, depthWrite: false,
      });
      const mesh = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), mat, v.capacity);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.count = 0;
      mesh.frustumCulled = false;
      this.meshes.set(v.key, mesh);
      this.scene.add(mesh);
    }

    this.rings = {
      hover: this._makeRing(0xffffff, 0.55),
      select: this._makeRing(0xffffff, 0.9),
      pulse: this._makeRing(0x0ca30c, 0.9),
    };
  }

  _makeRing(color, opacity) {
    const geo = new THREE.RingGeometry(0.44, 0.5, 48);
    const mat = new THREE.MeshBasicMaterial({
      color, transparent: true, opacity, depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.visible = false;
    mesh.position.z = 1;
    this.scene.add(mesh);
    return mesh;
  }

  /** Fixed slot assignment: id -> instance index within its variant's mesh. */
  assignSlots(items) {
    const counts = new Map();
    for (const { id, variant } of items) {
      const mesh = this.meshes.get(variant);
      const index = counts.get(variant) ?? 0;
      counts.set(variant, index + 1);
      this.slots.set(id, { mesh, index });
    }
    for (const [key, mesh] of this.meshes) mesh.count = counts.get(key) ?? 0;
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.aspect = w / h;
    this._applyCamera();
  }

  _applyCamera() {
    const { cx, cy, vh } = this.view;
    const hw = vh * this.aspect / 2;
    this.camera.left = cx - hw; this.camera.right = cx + hw;
    this.camera.top = cy + vh / 2; this.camera.bottom = cy - vh / 2;
    this.camera.updateProjectionMatrix();
  }

  fitBounds(b, { pad = 1.08, animate = true, delay = 0, duration = 900, now = performance.now() } = {}) {
    const w = b.maxX - b.minX, h = b.maxY - b.minY;
    const to = {
      cx: (b.minX + b.maxX) / 2, cy: (b.minY + b.maxY) / 2,
      vh: Math.max(h, w / this.aspect) * pad,
    };
    if (!animate) { this.view = to; this._applyCamera(); return; }
    this.camTween = { from: { ...this.view }, to, start: now + delay, duration, ease: easeCircInOut };
  }

  zoomBy(factor, clientX, clientY, { duration = 220, now = performance.now() } = {}) {
    const pivot = this.unproject(clientX, clientY);
    const vh = Math.max(20, Math.min(20000, this.view.vh * factor));
    const s = vh / this.view.vh;
    const to = {
      vh,
      cx: pivot.x + (this.view.cx - pivot.x) * s,
      cy: pivot.y + (this.view.cy - pivot.y) * s,
    };
    this.camTween = { from: { ...this.view }, to, start: now, duration, ease: easeCubicOut };
  }

  panBy(dxPx, dyPx) {
    const perPx = this.view.vh / this.canvas.clientHeight;
    this.camTween = null;
    this.view.cx -= dxPx * perPx;
    this.view.cy += dyPx * perPx;
    this._applyCamera();
  }

  unproject(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    const nx = (clientX - r.left) / r.width * 2 - 1;
    const ny = -((clientY - r.top) / r.height * 2 - 1);
    return {
      x: (this.camera.left + this.camera.right) / 2 + nx * (this.camera.right - this.camera.left) / 2,
      y: (this.camera.top + this.camera.bottom) / 2 + ny * (this.camera.top - this.camera.bottom) / 2,
    };
  }

  project(wx, wy) {
    const r = this.canvas.getBoundingClientRect();
    const nx = (wx - this.camera.left) / (this.camera.right - this.camera.left);
    const ny = (wy - this.camera.bottom) / (this.camera.top - this.camera.bottom);
    return { x: nx * r.width, y: (1 - ny) * r.height };
  }

  /** world size of one CSS pixel (for label visibility thresholds) */
  worldPerPixel() { return this.view.vh / this.canvas.clientHeight; }

  setColumns(columns) {
    this.columnGroup.clear();
    for (const c of columns) {
      if (!c.shaded) continue;
      const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(c.width, c.height),
        new THREE.MeshBasicMaterial({ color: SHADE, depthWrite: false }));
      mesh.position.set(c.x + c.width / 2, c.y + c.height / 2, -2);
      this.columnGroup.add(mesh);
    }
  }

  _placeRing(ring, state, scale = 1.25) {
    ring.visible = true;
    ring.position.x = state.x;
    ring.position.y = state.y;
    ring.scale.setScalar(state.size * scale);
  }

  /** Draw one frame from animator state. Returns true if camera still moving. */
  draw(animItems, { hoverId, selectId, pulseId, now }) {
    let camActive = false;
    if (this.camTween) {
      const tw = this.camTween;
      const t = (now - tw.start) / tw.duration;
      if (t >= 1) { this.view = { ...tw.to }; this.camTween = null; }
      else if (t > 0) {
        const e = tw.ease(t);
        this.view = {
          cx: tw.from.cx + (tw.to.cx - tw.from.cx) * e,
          cy: tw.from.cy + (tw.to.cy - tw.from.cy) * e,
          vh: tw.from.vh + (tw.to.vh - tw.from.vh) * e,
        };
      }
      camActive = this.camTween !== null;
      this._applyCamera();
    }

    for (const mesh of this.meshes.values()) mesh._touched = false;
    for (const [id, { mesh, index }] of this.slots) {
      const s = animItems.get(id);
      if (s) {
        this._mat4.makeScale(s.size, s.size, 1);
        this._mat4.setPosition(s.x, s.y, 0);
        this._color.setRGB(s.r, s.g, s.b, THREE.SRGBColorSpace);
      } else {
        this._mat4.makeScale(0, 0, 0);
        this._color.setRGB(0, 0, 0);
      }
      mesh.setMatrixAt(index, this._mat4);
      mesh.setColorAt(index, this._color);
      mesh._touched = true;
    }
    for (const mesh of this.meshes.values()) {
      if (!mesh._touched) continue;
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }

    const show = (ring, id, scale) => {
      const s = id !== null && animItems.get(id);
      s ? this._placeRing(ring, s, scale) : ring.visible = false;
    };
    show(this.rings.hover, hoverId !== selectId ? hoverId : null, 1.25);
    show(this.rings.select, selectId, 1.3);
    const pulseScale = 1.35 + 0.18 * Math.sin(now / 260);
    show(this.rings.pulse, pulseId !== selectId ? pulseId : null, pulseScale);

    this.renderer.render(this.scene, this.camera);
    return camActive || this.rings.pulse.visible;
  }
}
