import { useEffect, useRef } from "react";
import { ambientConfig, type AmbientConfig } from "./ambient.config";
import { Terrain, type AmbientState } from "./terrain";

/** The ASCII/wireframe mountain behind the chat workspace. A purely visual layer: it knows nothing about the
    model, tools or storage; the parent only tells it whether to animate. Fonts must be ready before the first
    build so the glyph atlas uses Geist Mono. */
export default function AmbientBackground({ state, reduced, config = ambientConfig }: { state: AmbientState; reduced: boolean; config?: AmbientConfig }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const terrain = useRef<Terrain | null>(null);

  useEffect(() => {
    const t = new Terrain(ref.current!, config);
    terrain.current = t;
    let alive = true;
    (document.fonts?.ready ?? Promise.resolve()).then(() => { if (alive) t.build(); });
    return () => { alive = false; t.destroy(); terrain.current = null; };
  }, [config]);

  useEffect(() => {
    /* Reduced motion, and phones (to save battery), get a single still frame instead of the animation. */
    const still = reduced || window.matchMedia("(prefers-reduced-motion: reduce), (max-width: 640px)").matches;
    terrain.current?.setState(state, still);
  }, [state, reduced]);

  return <canvas ref={ref} className={`ambient ambient-${state}`} aria-hidden="true" />;
}
