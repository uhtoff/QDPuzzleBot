import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { tipPosition } from "../lib/tipPosition";
import "./TipLayer.css";

/**
 * One shared tooltip for the whole app. Any element carrying a `data-tip`
 * attribute gets a styled tooltip on hover or keyboard focus, shown by this
 * single component through document-level listeners.
 *
 * Why not Mantine's <Tooltip>: each one mounts its own Floating-UI engine,
 * and the hunt board has several per row — hundreds idle at once. `data-tip`
 * costs a row nothing. Keep <Tooltip> for one-offs; use `data-tip` on
 * anything repeated per row or card. Pair it with an `aria-label`, since the
 * tooltip itself is not exposed to assistive tech.
 */
export function TipLayer() {
  const [tip, setTip] = useState<{ el: Element; text: string } | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const show = (target: EventTarget | null) => {
      const el = target instanceof Element ? target.closest("[data-tip]") : null;
      const text = el?.getAttribute("data-tip") ?? "";
      setTip((prev) => {
        if (!el || !text) return null;
        return prev && prev.el === el && prev.text === text ? prev : { el, text };
      });
    };
    const hide = () => setTip(null);

    const onPointerOver = (e: PointerEvent) => {
      // Touch has no hover: a tap would leave the tooltip stuck on screen.
      if (e.pointerType === "touch") return;
      show(e.target);
    };
    const onFocusIn = (e: FocusEvent) => {
      // Keyboard focus only — a mouse click also focuses, and must not pin it.
      if (e.target instanceof Element && e.target.matches(":focus-visible")) show(e.target);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };

    document.addEventListener("pointerover", onPointerOver);
    document.addEventListener("pointerdown", hide);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", hide);
    document.addEventListener("keydown", onKeyDown);
    // Capture: scrolls inside any container move the anchor too.
    window.addEventListener("scroll", hide, { capture: true, passive: true });
    window.addEventListener("blur", hide);
    // Leaving the page fires no pointerover on anything else.
    document.documentElement.addEventListener("pointerleave", hide);
    return () => {
      document.documentElement.removeEventListener("pointerleave", hide);
      document.removeEventListener("pointerover", onPointerOver);
      document.removeEventListener("pointerdown", hide);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", hide, { capture: true });
      window.removeEventListener("blur", hide);
    };
  }, []);

  // Position after render, once the tooltip's own size is known; written
  // straight to the node so placing it never costs a second render.
  useLayoutEffect(() => {
    const node = ref.current;
    if (!tip || !node) return;
    if (!tip.el.isConnected) {
      setTip(null);
      return;
    }
    const pos = tipPosition(
      tip.el.getBoundingClientRect(),
      { width: node.offsetWidth, height: node.offsetHeight },
      document.documentElement.clientWidth,
    );
    node.style.transform = `translate(${Math.round(pos.left)}px, ${Math.round(pos.top)}px)`;
  }, [tip]);

  if (!tip) return null;
  return createPortal(
    <div ref={ref} className="tip-layer" role="presentation">
      {tip.text}
    </div>,
    document.body,
  );
}
