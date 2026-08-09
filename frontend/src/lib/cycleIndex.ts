/**
 * Step an index around a list, wrapping in BOTH directions.
 *
 * The naive `(i + delta) % length` is correct going forward and wrong going
 * back: JS's `%` keeps the sign of the dividend, so stepping back from index 0
 * gives -1 rather than the last item. Hence the double modulo.
 *
 * A `current` of -1 (an `indexOf` miss) is tolerated: it steps to 0 going
 * forward, which is the sane recovery if the current value ever leaves the list.
 */
export function cycleIndex(current: number, delta: number, length: number): number {
  if (length <= 0) return 0;
  return (((current + delta) % length) + length) % length;
}
