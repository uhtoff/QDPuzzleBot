import { useEffect, useState } from "react";

/** Wires up ctrl+wheel (trackpad pinch is reported by browsers as a wheel
 *  event with ctrlKey set) to zoom about the cursor. A raw DOM listener, NOT
 *  React's onWheel: the synthetic wheel listener is passive, so preventDefault
 *  silently no-ops there and the browser's own page zoom fires underneath.
 *
 *  Extracted from CanvasView and MarkupCanvas, which wired this part up
 *  identically — only the `zoomAt` closure they pass in differs.
 *
 *  Returns a **callback ref** to put on the element, rather than taking a
 *  RefObject. A RefObject is filled in during commit without re-running any
 *  effect, so if the element is not in the tree on the render that mounts this
 *  hook, `ref.current` is null when the effect runs and nothing ever runs it
 *  again — the listener is never attached, permanently. That is not
 *  hypothetical: CanvasView renders a spinner while a canvas is still tiling
 *  and swaps in the real container *in place* when it flips to ready, so
 *  ctrl+wheel zoom was dead for the whole life of any canvas watched through
 *  that transition. A callback ref fires whenever the node itself appears or
 *  goes away, so attachment tracks the element instead of the mount. */
export function useWheelZoom(
  zoomAt: (factor: number, anchorX: number, anchorY: number) => void,
): (el: HTMLElement | null) => void {
  // State, not a ref: the point is to re-run the effect when the node changes.
  // setEl is identity-stable, so it is safe to use directly as the callback ref
  // (a fresh closure each render would detach and reattach on every render).
  const [el, setEl] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      // 0.0015, not 0.01: a mouse wheel reports deltaY of ~100 per detent, so
      // the old factor was e^1 — one click zoomed 2.7x and flew straight past
      // whatever you were aiming at. ~1.16x per detent, and trackpad deltas
      // (which are much smaller) stay smooth.
      const factor = Math.exp(-e.deltaY * 0.0015);
      zoomAt(factor, e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [el, zoomAt]);

  return setEl;
}
