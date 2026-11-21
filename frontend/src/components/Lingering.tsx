import type { ReactNode } from "react";
import { useLingering } from "../hooks/useLingering";

/**
 * Render-prop form of useLingering, for a component with several dialogs:
 * mounts `children` only while `value` is set (plus the close fade), handing
 * it the value to show and the `opened` flag to pass to the dialog.
 *
 *   <Lingering value={deleteTarget}>
 *     {(target, opened) => <ConfirmModal opened={opened} title={target.name} … />}
 *   </Lingering>
 */
export function Lingering<T>({
  value,
  children,
}: {
  value: T | null | undefined;
  children: (value: T, opened: boolean) => ReactNode;
}) {
  const shown = useLingering(value);
  if (shown.value == null) return null;
  return <>{children(shown.value, shown.opened)}</>;
}
