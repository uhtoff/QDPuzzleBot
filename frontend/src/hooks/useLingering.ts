import { useEffect, useState } from "react";

/**
 * Keep a dialog (or any overlay) mounted only while it is in use, without
 * losing its open/close animation.
 *
 * Pass the thing the overlay is for (`null`/`undefined` = closed). Render the
 * overlay while the returned `value` is non-null, reading its contents from
 * that `value`, and pass the returned `opened` as its `opened` prop:
 *
 *   const chat = useLingering(chatPuzzle);
 *   {chat.value && <ChatViewer opened={chat.opened} title={chat.value.name} … />}
 *
 * On open, `opened` turns true just after mount, so the enter transition
 * plays. On close, `value` holds the last target for `ms` with `opened`
 * false, so the exit transition plays before the overlay unmounts.
 */
export function useLingering<T>(
  value: T | null | undefined,
  ms = 220,
): { value: T | null; opened: boolean } {
  const [held, setHeld] = useState<T | null>(value ?? null);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    if (value != null) {
      setHeld(value);
      // A timer, not requestAnimationFrame: frames stop in a background tab,
      // which would leave the overlay mounted but never opened.
      const enter = setTimeout(() => setEntered(true), 0);
      return () => clearTimeout(enter);
    }
    setEntered(false);
    const timer = setTimeout(() => setHeld(null), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);

  return { value: value ?? held, opened: value != null && entered };
}
