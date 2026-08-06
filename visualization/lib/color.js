// Color math for murmuration: sRGB <-> OKLab/OKLCH, ramp generation, blending.
// Blending happens in OKLab (perceptually uniform), so mixing two gradients
// gives a sensible in-between instead of muddy RGB averages.

export function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}

export function rgbToHex([r, g, b]) {
  const c = v => Math.round(Math.max(0, Math.min(1, v)) * 255)
    .toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

const srgbToLinear = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const linearToSrgb = c => c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;

export function rgbToOklab([r, g, b]) {
  const [lr, lg, lb] = [srgbToLinear(r), srgbToLinear(g), srgbToLinear(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ];
}

export function oklabToRgb([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  return [
    linearToSrgb(+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
  ];
}

const oklabToLch = ([L, a, b]) =>
  [L, Math.hypot(a, b), Math.atan2(b, a)];
const lchToOklab = ([L, C, h]) =>
  [L, C * Math.cos(h), C * Math.sin(h)];

/**
 * One-hue sequential ramp from a base color, tuned for a dark surface:
 * t=0 recedes toward the surface (dim, low chroma), t=1 is bright.
 * Returns t -> [r,g,b].
 */
export function makeRamp(baseHex, { lMin = 0.38, lMax = 0.85, cMin = 0.25 } = {}) {
  const [, C, h] = oklabToLch(rgbToOklab(hexToRgb(baseHex)));
  return t => {
    const tt = Math.max(0, Math.min(1, t));
    const L = lMin + (lMax - lMin) * tt;
    const chroma = C * (cMin + (1 - cMin) * tt);
    return oklabToRgb(lchToOklab([L, chroma, h]));
  };
}

/** Two-point ramp between fixed colors (for booleans / dim-bright defaults). */
export function makeTwoColorRamp(hexA, hexB) {
  const a = rgbToOklab(hexToRgb(hexA)), b = rgbToOklab(hexToRgb(hexB));
  return t => oklabToRgb(a.map((v, i) => v + (b[i] - v) * Math.max(0, Math.min(1, t))));
}

/** Average N RGB colors in OKLab space. */
export function blendRgb(colors) {
  if (colors.length === 1) return colors[0];
  const acc = [0, 0, 0];
  for (const c of colors) {
    const lab = rgbToOklab(c);
    acc[0] += lab[0]; acc[1] += lab[1]; acc[2] += lab[2];
  }
  return oklabToRgb(acc.map(v => v / colors.length));
}

/** Interpolate two RGB colors in OKLab (for color tweens). */
export function lerpRgb(a, b, t) {
  const la = rgbToOklab(a), lb = rgbToOklab(b);
  return oklabToRgb(la.map((v, i) => v + (lb[i] - v) * t));
}
