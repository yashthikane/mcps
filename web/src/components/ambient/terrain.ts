/* Generative wireframe + ASCII mountain terrain on a 2D canvas (spec §5).

   A heightfield is projected in perspective and drawn as a wireframe, far to near, filling each strip with the
   panel colour so nearer terrain hides what is behind it. That mesh, the glyph positions, the vignette and the
   mist strip are built once per resize. An animated frame only redraws the wind-modulated ASCII glyphs, the
   mist and a few particles on top of the cached mesh, at no more than `fps`.

   States: "idle" animates; "paused" freezes on the current frame (time is kept, so resuming never resets);
   "disabled" draws nothing. The loop also stops while the tab is hidden or under reduced motion. */
import type { AmbientConfig } from "./ambient.config";

export type AmbientState = "idle" | "paused" | "disabled";

const GLYPHS = "^/\\:.+*'`-";
const LEVELS = 7;

const rng = (seed: number) => () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };

/** Smooth 2D value noise on a wrapping lattice; precomputed so a frame does no random generation. */
function noise2(seed: number, W = 128, H = 64) {
  const r = rng(seed), v = Float32Array.from({ length: W * H }, r);
  const g = (a: number, b: number) => v[(((b % H) + H) % H) * W + (((a % W) + W) % W)];
  return (x: number, y: number) => {
    const xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
    const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
    const a = g(xi, yi), b = g(xi + 1, yi), c = g(xi, yi + 1), d = g(xi + 1, yi + 1);
    return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
  };
}
const hash = (x: number, y: number) => {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
};

interface Particle { x: number; y: number; v: number; p: number; s: number; c: number }

export class Terrain {
  private ctx: CanvasRenderingContext2D;
  private state: AmbientState = "paused";
  private t = 0;
  private raf = 0;
  private last = 0;
  private acc = 0;
  private reduced = false;
  private readonly detail = noise2(3);
  private readonly wind = noise2(8);
  private ro: ResizeObserver;
  private rz = 0;
  private W = 0; private H = 0; private dpr = 1; private hz = 0;
  private mesh?: HTMLCanvasElement; private vig?: HTMLCanvasElement; private fog?: HTMLCanvasElement; private atlas?: HTMLCanvasElement;
  private glyphs: number[] = [];
  private parts: Particle[] = [];
  private cw = 7; private chh = 11;

  constructor(private canvas: HTMLCanvasElement, private cfg: AmbientConfig) {
    this.ctx = canvas.getContext("2d")!;
    this.ro = new ResizeObserver(() => { clearTimeout(this.rz); this.rz = window.setTimeout(() => this.build(), 120); });
    this.ro.observe(canvas.parentElement ?? canvas);
    document.addEventListener("visibilitychange", this.sync);
  }

  destroy() {
    cancelAnimationFrame(this.raf); this.raf = 0;
    clearTimeout(this.rz); this.ro.disconnect();
    document.removeEventListener("visibilitychange", this.sync);
  }

  setState(s: AmbientState, reduced: boolean) { this.state = s; this.reduced = reduced; this.sync(); }

  private height(x: number, z: number) {
    let h = 0;
    for (const [px, pz, sx, sz, ph] of this.cfg.peaks) h += ph * Math.exp(-(((x - px) / sx) ** 2 + ((z - pz) / sz) ** 2));
    if (h < 0.02) return 0;
    const rough = this.detail(x * 1.7 + 40, z * 1.7 + 20), ridge = 1 - Math.abs(2 * this.detail(x * 3.1, z * 3.1 + 9) - 1);
    return h * (0.72 + 0.38 * rough) + h * 0.22 * ridge;
  }

  build() {
    const cfg = this.cfg, cam = cfg.camera, box = (this.canvas.parentElement ?? this.canvas).getBoundingClientRect();
    if (!box.width || !box.height) return;
    const dpr = this.dpr = Math.min(window.devicePixelRatio || 1, cfg.maxDpr);
    const W = this.W = Math.round(box.width * dpr), H = this.H = Math.round(box.height * dpr);
    this.canvas.width = W; this.canvas.height = H;
    const hz = this.hz = H * cam.horizon, FX = W * cam.fx, FY = H * cam.fy;

    /* sample and project the grid: rows go far → near */
    const zs: number[] = []; for (let z = cam.zFar; z >= cam.zNear; z -= cam.dz) zs.push(z);
    const xs: number[] = []; for (let x = -cam.xMax; x <= cam.xMax + 1e-6; x += cam.dx) xs.push(x);
    const R = zs.length, C = xs.length, P = new Float32Array(R * C * 3);
    for (let i = 0; i < R; i++) for (let j = 0; j < C; j++) {
      const h = this.height(xs[j], zs[i]), k = (i * C + j) * 3;
      P[k] = W / 2 + xs[j] * FX / zs[i]; P[k + 1] = hz + (cam.height - h) * FY / zs[i]; P[k + 2] = h;
    }
    /* brightness: nearer and higher is brighter, so the peaks stay lit while the base fades */
    const alpha = (h: number, z: number) => {
      const depth = Math.max(0.25, 1.08 - (z - cam.zNear) / (cam.zFar - cam.zNear) * 0.7);
      return depth * (h < 0.03 ? 0.2 : 0.32 + 0.68 * Math.min(1, h / 1.6));
    };

    /* wireframe mesh with painter's-order occlusion */
    const mesh = this.mesh = document.createElement("canvas"); mesh.width = W; mesh.height = H;
    const g = mesh.getContext("2d")!, [ir, ig, ib] = cfg.ink;
    g.lineWidth = Math.max(1, dpr * 0.75); g.lineJoin = "round";
    for (let i = 0; i < R - 1; i++) {
      const a = i * C * 3, b = (i + 1) * C * 3;
      g.beginPath(); g.moveTo(P[a], P[a + 1]);
      for (let j = 1; j < C; j++) g.lineTo(P[a + j * 3], P[a + j * 3 + 1]);
      for (let j = C - 1; j >= 0; j--) g.lineTo(P[b + j * 3], P[b + j * 3 + 1]);
      g.closePath(); g.fillStyle = cfg.panel; g.fill();
      if (cfg.style === "ascii") continue;
      const buckets = Array.from({ length: LEVELS }, () => new Path2D());
      for (let j = 0; j < C - 1; j++) {
        const p = a + j * 3, q = p + 3, s = b + j * 3;
        const l1 = Math.min(LEVELS - 1, Math.floor(alpha((P[p + 2] + P[q + 2]) / 2, zs[i]) * LEVELS));
        buckets[l1].moveTo(P[p], P[p + 1]); buckets[l1].lineTo(P[q], P[q + 1]);
        const l2 = Math.min(LEVELS - 1, Math.floor(alpha((P[p + 2] + P[s + 2]) / 2, (zs[i] + zs[i + 1]) / 2) * LEVELS));
        buckets[l2].moveTo(P[p], P[p + 1]); buckets[l2].lineTo(P[s], P[s + 1]);
      }
      buckets.forEach((path, l) => { g.strokeStyle = `rgba(${ir},${ig},${ib},${((l + 1) / LEVELS) * cfg.lineAlpha})`; g.stroke(path); });
    }
    /* soft horizon light between the ranges */
    g.save(); g.translate(W / 2, hz + H * 0.015); g.scale(1, 0.12);
    const mist = g.createRadialGradient(0, 0, 0, 0, 0, W * 0.3);
    mist.addColorStop(0, "rgba(220,222,230,.13)"); mist.addColorStop(1, "rgba(220,222,230,0)");
    g.fillStyle = mist; g.fillRect(-W * 0.3, -W * 0.3, W * 0.6, W * 0.6); g.restore();

    /* ASCII glyphs on vertices that are actually visible (a y-buffer built near → far) */
    const ybuf = new Float32Array(W).fill(H * 2), glyphs: number[] = [];
    const ascii = cfg.style === "ascii", step = ascii ? 1 : 2;
    for (let i = R - 1; i >= 0; i--) {
      const a = i * C * 3;
      if (cfg.style !== "wire" && (ascii || i % 2 === 0)) {
        for (let j = 1; j < C - 1; j += step) {
          const k = a + j * 3, h = P[k + 2], sx = P[k], sy = P[k + 1], xi = Math.round(sx);
          if (xi < 0 || xi >= W || sy < 0 || sy > H) continue;
          const floor = h < 0.05;
          if (floor && (!ascii || hash(i, j) > 0.2)) continue;
          if (sy > ybuf[xi] - dpr * 2) continue;
          const hl = P[k - 1], hr = P[k + 5], ht = i > 0 ? P[(i - 1) * C * 3 + j * 3 + 2] : h;
          let ch = ".";
          if (!floor) {
            const sl = hr - hl;
            ch = h > hl && h > hr && h >= ht ? "^" : sl > 0.07 ? "/" : sl < -0.07 ? "\\" : hash(j, i) < 0.5 ? ":" : "+";
            if (h < 0.25) ch = hash(i, j) < 0.5 ? "." : "'";
          }
          glyphs.push(sx, sy, GLYPHS.indexOf(ch), alpha(h, zs[i]) * (floor ? 0.7 : 1.15));
        }
      }
      for (let j = 0; j < C - 1; j++) {
        const p = a + j * 3, q = p + 3;
        const x0 = Math.max(0, Math.ceil(Math.min(P[p], P[q]))), x1 = Math.min(W - 1, Math.floor(Math.max(P[p], P[q])));
        for (let x = x0; x <= x1; x++) {
          const y = P[p + 1] + (P[q + 1] - P[p + 1]) * ((x - P[p]) / (P[q] - P[p] || 1));
          if (y < ybuf[x]) ybuf[x] = y;
        }
      }
    }
    this.glyphs = glyphs;

    /* glyph atlas: one row per brightness level, so a frame only does drawImage */
    const cw = this.cw = Math.round(7 * dpr), chh = this.chh = Math.round(11 * dpr);
    const at = this.atlas = document.createElement("canvas"); at.width = GLYPHS.length * cw; at.height = LEVELS * chh;
    const ac = at.getContext("2d")!;
    ac.font = `500 ${Math.round(9.5 * dpr)}px "Geist Mono", ui-monospace, monospace`; ac.textAlign = "center"; ac.textBaseline = "middle";
    ac.fillStyle = `rgb(${ir},${ig},${ib})`;
    for (let l = 0; l < LEVELS; l++) { ac.globalAlpha = (l + 1) / LEVELS; for (let i = 0; i < GLYPHS.length; i++) ac.fillText(GLYPHS[i], i * cw + cw / 2, l * chh + chh / 2); }

    /* vignette: dark bottom corners and a bottom fade keep the composer in focus */
    const vig = this.vig = document.createElement("canvas"); vig.width = W; vig.height = H;
    const vg = vig.getContext("2d")!, n = parseInt(cfg.panel.slice(1), 16);
    const rgba = (al: number) => `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${al})`;
    const bf = vg.createLinearGradient(0, hz - H * 0.02, 0, H);
    bf.addColorStop(0, rgba(0)); bf.addColorStop(0.45, rgba(cfg.vignette.bottom * 0.55)); bf.addColorStop(1, rgba(cfg.vignette.bottom));
    vg.fillStyle = bf; vg.fillRect(0, 0, W, H);
    for (const cx of [0, W]) {
      const rg = vg.createRadialGradient(cx, H, 0, cx, H, Math.max(W * 0.42, H * 0.7));
      rg.addColorStop(0, rgba(cfg.vignette.corners)); rg.addColorStop(0.55, rgba(cfg.vignette.corners * 0.55)); rg.addColorStop(1, rgba(0));
      vg.fillStyle = rg; vg.fillRect(0, 0, W, H);
    }

    /* mist strip (2× wide so it can drift and wrap) and particles */
    const fog = this.fog = document.createElement("canvas"); fog.width = W * 2; fog.height = Math.max(1, Math.round(H * 0.12));
    const f = fog.getContext("2d")!, fr = rng(77);
    for (let j = 0; j < 12; j++) {
      const cx = fr() * fog.width, cy = fog.height * (0.35 + fr() * 0.3), rx = W * (0.08 + fr() * 0.12), ry = fog.height * (0.25 + fr() * 0.2);
      const gr = f.createRadialGradient(0, 0, 0, 0, 0, 1);
      gr.addColorStop(0, "rgba(210,212,220,.09)"); gr.addColorStop(1, "rgba(210,212,220,0)");
      f.save(); f.translate(cx, cy); f.scale(rx, ry); f.fillStyle = gr; f.beginPath(); f.arc(0, 0, 1, 0, Math.PI * 2); f.fill(); f.restore();
    }
    const pr = rng(91);
    this.parts = Array.from({ length: cfg.particles }, () => ({
      x: pr() * W, y: hz - H * (0.05 + pr() * 0.3), v: (4 + pr() * 8) * dpr, p: pr(), s: 0.03 + pr() * 0.05, c: GLYPHS.indexOf(".+'"[Math.floor(pr() * 3)]),
    }));
    this.draw();
  }

  private draw() {
    const { ctx, cfg } = this;
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, this.W, this.H);
    if (!this.mesh || !this.atlas || !this.vig || !this.fog || this.state === "disabled" || cfg.visibility <= 0) return;
    const vis = Math.min(1, cfg.visibility), t = this.t, cw = this.cw, chh = this.chh, at = this.atlas;
    ctx.globalAlpha = vis * (0.9 + 0.1 * Math.sin(t * 0.3));
    ctx.drawImage(this.mesh, 0, 0);
    ctx.globalAlpha = vis;
    const G = this.glyphs;
    for (let i = 0; i < G.length; i += 4) {
      const n = this.wind((G[i] * 0.006) / this.dpr - t * 0.22, (G[i + 1] * 0.012) / this.dpr);
      let l = Math.round(G[i + 3] * (0.45 + 0.75 * n) * LEVELS) - 1;
      if (l < 0) continue;
      if (l >= LEVELS) l = LEVELS - 1;
      ctx.drawImage(at, G[i + 2] * cw, l * chh, cw, chh, Math.round(G[i] - cw / 2), Math.round(G[i + 1] - chh / 2), cw, chh);
    }
    ctx.globalAlpha = vis * 0.8;
    ctx.drawImage(this.fog, -((t * 8 * this.dpr) % this.W), this.hz - this.fog.height * 0.6);
    for (const p of this.parts) {
      const a = Math.sin(Math.PI * p.p);
      if (a < 0.1) continue;
      ctx.globalAlpha = vis * a * 0.55;
      ctx.drawImage(at, p.c * cw, (LEVELS - 1) * chh, cw, chh, Math.round(p.x), Math.round(p.y), cw, chh);
    }
    ctx.globalAlpha = 1;
    ctx.drawImage(this.vig, 0, 0);
  }

  private step(dt: number) {
    this.t += dt;
    for (const p of this.parts) {
      p.x += p.v * dt; p.p += p.s * dt;
      if (p.p >= 1 || p.x > this.W) { p.p = 0; p.x = Math.random() * this.W * 0.9; p.y = this.hz - this.H * (0.05 + Math.random() * 0.3); }
    }
  }

  private loop = (now: number) => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(0.1, (now - (this.last || now)) / 1000);
    this.last = now; this.acc += dt;
    if (this.acc < 1 / this.cfg.fps) return;
    this.step(this.acc); this.acc = 0; this.draw();
  };

  private sync = () => {
    const run = this.state === "idle" && !document.hidden && !this.reduced && this.cfg.visibility > 0;
    if (run && !this.raf) { this.last = 0; this.raf = requestAnimationFrame(this.loop); }
    if (!run && this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
    if (!run) this.draw();
  };
}
