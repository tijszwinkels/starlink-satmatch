// WebGL renderer: every item is one instance of an InstancedMesh (one mesh
// per icon variant), tinted via per-instance color and billboarded toward
// the camera. One perspective camera serves two modes:
//   'data'  — faces the z=0 layout plane, driven by {cx, cy, vh} (pan/zoom)
//   'orbit' — orbits the origin, driven by {theta, phi, radius} (drag/zoom)
// Mode changes fly the camera (position + look-target lerp) while the
// choreography flies the items, so orbit <-> data reads as one motion.

import * as THREE from "./vendor/three.module.js";
import { easeCubicOut, easeCircInOut } from "./animate.js";

const SURFACE = 0x131312;
const SHADE = 0x1d1d1c;
const FOV = 30;
const HALF = Math.tan(THREE.MathUtils.degToRad(FOV / 2));

export class Renderer {
  constructor(canvas, { variants, orbitDefaults }) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(SURFACE);
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 1, 100000);

    this.mode = "data";
    this.view = { cx: 0, cy: 0, vh: 1200 };                    // data mode
    this.orbit = { theta: 0, phi: 1.1, radius: 2400, ...orbitDefaults };
    this.stateTween = null;   // tween over this.view (pan/zoom/fit in data)
    this.camTween = null;     // camera flight between modes

    this.meshes = new Map();
    this.slots = new Map();
    this.columnGroup = new THREE.Group();
    this.scene.add(this.columnGroup);
    this._mat4 = new THREE.Matrix4();
    this._rotM = new THREE.Matrix4();
    this._color = new THREE.Color();
    this._v3 = new THREE.Vector3();

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
    this.scene.add(mesh);
    return mesh;
  }

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
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this._applyCamera();
  }

  // ------------------------------------------------------------- camera

  _dataCam(view) {
    const d = view.vh / (2 * HALF);
    return {
      pos: new THREE.Vector3(view.cx, view.cy, d),
      target: new THREE.Vector3(view.cx, view.cy, 0),
    };
  }

  _orbitCam(o) {
    return {
      pos: new THREE.Vector3(
        o.radius * Math.sin(o.phi) * Math.cos(o.theta),
        o.radius * Math.cos(o.phi),
        o.radius * Math.sin(o.phi) * Math.sin(o.theta)),
      target: new THREE.Vector3(0, 0, 0),
    };
  }

  _applyCamera() {
    const { pos, target } = this.mode === "orbit"
      ? this._orbitCam(this.orbit) : this._dataCam(this.view);
    this.camera.position.copy(pos);
    this.camera.lookAt(target);
  }

  /** Fly the camera to the current mode-state of `toMode`. */
  _flyTo(toMode, { delay = 0, duration = 900, now }) {
    const to = toMode === "orbit"
      ? this._orbitCam(this.orbit) : this._dataCam(this.view);
    this.camTween = {
      fromPos: this.camera.position.clone(),
      fromTarget: this._currentTarget(),
      toPos: to.pos, toTarget: to.target,
      start: now + delay, duration, ease: easeCircInOut, toMode,
    };
    this.stateTween = null;
  }

  _currentTarget() {
    // reconstruct the look-target from the camera's forward direction
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    const dist = this.mode === "orbit"
      ? this.camera.position.length()
      : this.view.vh / (2 * HALF);
    return this.camera.position.clone().addScaledVector(dir, dist);
  }

  _settleTweens() {
    if (this.camTween) { this.mode = this.camTween.toMode; this.camTween = null; }
    if (this.stateTween) { this.view = { ...this.stateTween.to }; this.stateTween = null; }
    this._applyCamera();
  }

  fitBounds(b, { pad = 1.08, animate = true, delay = 0, duration = 900, now = performance.now() } = {}) {
    const w = b.maxX - b.minX, h = b.maxY - b.minY;
    const aspect = this.camera.aspect || 1;
    const to = {
      cx: (b.minX + b.maxX) / 2, cy: (b.minY + b.maxY) / 2,
      vh: Math.max(h, w / aspect) * pad,
    };
    if (this.mode === "orbit" || this.camTween) {
      this.view = to;
      this._flyTo("data", { delay, duration, now });
      return;
    }
    if (!animate) { this.view = to; this._applyCamera(); return; }
    this.stateTween = { from: { ...this.view }, to, start: now + delay, duration, ease: easeCircInOut };
  }

  enterOrbit({ delay = 0, duration = 900, now = performance.now() } = {}) {
    if (this.mode === "orbit" && !this.camTween) return;
    this._flyTo("orbit", { delay, duration, now });
  }

  zoomBy(factor, clientX, clientY, { duration = 220, now = performance.now() } = {}) {
    if (this.camTween) this._settleTweens();
    if (this.mode === "orbit") {
      this.orbit.radius = Math.max(750, Math.min(9000, this.orbit.radius * factor));
      this._applyCamera();
      return;
    }
    const pivot = this.unproject(clientX, clientY);
    const vh = Math.max(20, Math.min(20000, this.view.vh * factor));
    const s = vh / this.view.vh;
    const to = {
      vh,
      cx: pivot.x + (this.view.cx - pivot.x) * s,
      cy: pivot.y + (this.view.cy - pivot.y) * s,
    };
    this.stateTween = { from: { ...this.view }, to, start: now, duration, ease: easeCubicOut };
  }

  panBy(dxPx, dyPx) {
    if (this.camTween) this._settleTweens();
    this.stateTween = null;
    if (this.mode === "orbit") {
      this.orbit.theta += dxPx * 0.005;
      this.orbit.phi = Math.max(0.15, Math.min(Math.PI - 0.15,
        this.orbit.phi - dyPx * 0.005));
    } else {
      const perPx = this.view.vh / this.canvas.clientHeight;
      this.view.cx -= dxPx * perPx;
      this.view.cy += dyPx * perPx;
    }
    this._applyCamera();
  }

  // ------------------------------------------------------- projections

  /** Pointer -> world point on the z=0 layout plane (data views). */
  unproject(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    const ndc = this._v3.set(
      (clientX - r.left) / r.width * 2 - 1,
      -((clientY - r.top) / r.height * 2 - 1), 0.5);
    ndc.unproject(this.camera);
    const dir = ndc.sub(this.camera.position).normalize();
    const t = -this.camera.position.z / dir.z;
    return {
      x: this.camera.position.x + dir.x * t,
      y: this.camera.position.y + dir.y * t,
    };
  }

  project(wx, wy, wz = 0) {
    const r = this.canvas.getBoundingClientRect();
    const v = this._v3.set(wx, wy, wz).project(this.camera);
    return { x: (v.x + 1) / 2 * r.width, y: (1 - v.y) / 2 * r.height, behind: v.z > 1 };
  }

  /** Nearest item to the pointer in screen space (orbit-view picking). */
  pickScreen(clientX, clientY, animItems, threshold = 14) {
    const r = this.canvas.getBoundingClientRect();
    const px = clientX - r.left, py = clientY - r.top;
    let best = null, bestD = threshold * threshold;
    for (const [id, s] of animItems) {
      const p = this.project(s.x, s.y, s.z ?? 0);
      if (p.behind) continue;
      const d = (p.x - px) ** 2 + (p.y - py) ** 2;
      if (d < bestD) { best = id; bestD = d; }
    }
    return best;
  }

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

  // -------------------------------------------------------------- frame

  _placeRing(ring, state, scale = 1.25) {
    ring.visible = true;
    ring.position.set(state.x, state.y, state.z ?? 0);
    ring.quaternion.copy(this.camera.quaternion);
    ring.scale.setScalar(state.size * scale);
  }

  draw(animItems, { hoverId, selectId, pulseId, now }) {
    if (this.camTween) {
      const tw = this.camTween;
      const t = (now - tw.start) / tw.duration;
      if (t >= 1) {
        this.mode = tw.toMode;
        this.camTween = null;
        this._applyCamera();
      } else if (t > 0) {
        const e = tw.ease(t);
        this.camera.position.lerpVectors(tw.fromPos, tw.toPos, e);
        this.camera.lookAt(this._v3.lerpVectors(tw.fromTarget, tw.toTarget, e));
      } else {
        this.camera.position.copy(tw.fromPos);
        this.camera.lookAt(tw.fromTarget);
      }
    } else if (this.stateTween) {
      const tw = this.stateTween;
      const t = (now - tw.start) / tw.duration;
      if (t >= 1) { this.view = { ...tw.to }; this.stateTween = null; }
      else if (t > 0) {
        const e = tw.ease(t);
        this.view = {
          cx: tw.from.cx + (tw.to.cx - tw.from.cx) * e,
          cy: tw.from.cy + (tw.to.cy - tw.from.cy) * e,
          vh: tw.from.vh + (tw.to.vh - tw.from.vh) * e,
        };
      }
      this._applyCamera();
    }

    // billboard: same camera-facing rotation for every instance
    this._rotM.makeRotationFromQuaternion(this.camera.quaternion);
    const e = this._rotM.elements;
    for (const mesh of this.meshes.values()) mesh._touched = false;
    for (const [id, { mesh, index }] of this.slots) {
      const s = animItems.get(id);
      if (s) {
        const k = s.size;
        this._mat4.set(
          e[0] * k, e[4] * k, e[8] * k, s.x,
          e[1] * k, e[5] * k, e[9] * k, s.y,
          e[2] * k, e[6] * k, e[10] * k, s.z ?? 0,
          0, 0, 0, 1);
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
    return this.camTween !== null || this.stateTween !== null;
  }
}
