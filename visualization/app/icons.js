// Satellite icon textures, drawn as white silhouettes (the renderer tints
// them per instance). Two recognizable-at-a-distance shapes:
//   v1  — Gen1 flat-panel bus with ONE solar wing standing up ("sail")
//   v2  — V2 Mini bus with TWO solar wings out to the sides
// plus a plain diamond for unknown hardware.

const SIZE = 128;

function makeCanvas(draw) {
  const c = document.createElement("canvas");
  c.width = c.height = SIZE;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#ffffff";
  draw(ctx);
  return c;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fill();
}

export function iconV1() {
  return makeCanvas(ctx => {
    // single solar sail above the bus, with visible panel grid
    roundRect(ctx, 40, 10, 48, 74, 5);
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(0,0,0,0.5)";
    for (let i = 1; i < 3; i++) {
      ctx.beginPath();
      ctx.moveTo(40 + i * 16, 12);
      ctx.lineTo(40 + i * 16, 82);
      ctx.stroke();
    }
    // strut + flat bus
    ctx.fillRect(61, 84, 6, 10);
    roundRect(ctx, 34, 94, 60, 22, 6);
  });
}

export function iconV2() {
  return makeCanvas(ctx => {
    // two wings out to the sides
    roundRect(ctx, 4, 48, 44, 30, 4);
    roundRect(ctx, 80, 48, 44, 30, 4);
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(0,0,0,0.5)";
    for (const x of [19, 34, 95, 110]) {
      ctx.beginPath();
      ctx.moveTo(x, 50);
      ctx.lineTo(x, 76);
      ctx.stroke();
    }
    // central bus, taller than wide
    roundRect(ctx, 50, 38, 28, 50, 6);
  });
}

export function iconUnknown() {
  return makeCanvas(ctx => {
    ctx.translate(SIZE / 2, SIZE / 2);
    ctx.rotate(Math.PI / 4);
    roundRect(ctx, -28, -28, 56, 56, 8);
  });
}

export const VARIANTS = [
  { key: "v1", image: iconV1() },
  { key: "v2", image: iconV2() },
  { key: "unknown", image: iconUnknown() },
];
