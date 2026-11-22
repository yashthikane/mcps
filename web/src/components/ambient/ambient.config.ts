/* All visual tuning for the ambient terrain lives here (spec §19), not in the renderer. */

export type AmbientStyle = "hybrid" | "wire" | "ascii";

export interface AmbientConfig {
  style: AmbientStyle;          // wireframe + ASCII glyphs, wireframe only, or glyphs only
  visibility: number;           // 0 hides the terrain; 1 is the tuned look
  fps: number;                  // the animation never runs faster than this
  maxDpr: number;               // device-pixel-ratio cap for the canvas
  camera: { horizon: number; height: number; fx: number; fy: number; zNear: number; zFar: number; dz: number; dx: number; xMax: number };
  /** Mountain blobs: x, z, width along x, width along z, height (world units). */
  peaks: [number, number, number, number, number][];
  ink: [number, number, number];  // monochrome line / glyph colour
  lineAlpha: number;               // brightest wireframe line
  panel: string;                   // the panel colour the terrain sits on (used to occlude and to fade)
  vignette: { corners: number; bottom: number };
  particles: number;
}

export const ambientConfig: AmbientConfig = {
  style: "hybrid",
  visibility: 1,
  fps: 24,
  maxDpr: 1.5,
  camera: { horizon: 0.74, height: 0.42, fx: 0.4, fy: 0.6, zNear: 0.72, zFar: 7.6, dz: 0.115, dx: 0.14, xMax: 9 },
  peaks: [
    [0.3, 5.5, 2.1, 1.0, 2.3], [-1.5, 6.1, 1.4, 0.9, 1.6], [2.2, 6.3, 1.4, 0.8, 1.45], [-4.1, 4.6, 1.7, 1.0, 1.75], [-2.7, 3.9, 1.0, 0.65, 0.95],
    [4.0, 4.8, 1.8, 0.9, 1.6], [2.9, 3.5, 1.0, 0.55, 0.85], [-2.0, 2.0, 0.75, 0.45, 0.42], [2.5, 2.35, 0.85, 0.5, 0.55],
    [-6.2, 5.6, 1.8, 1.1, 1.5], [6.4, 5.9, 1.8, 1.1, 1.4],
  ],
  ink: [214, 216, 222],
  lineAlpha: 0.5,
  panel: "#0B0C12",
  vignette: { corners: 0.95, bottom: 0.85 },
  particles: 28,
};
