/**
 * Tooltip formatting helpers for recharts v3.
 *
 * recharts 3 widened the `Tooltip` formatter signature: `value` is now
 * `ValueType | undefined` (a number, a string, or an array of either), so the
 * `(value: number) => ...` callbacks written against v2 no longer typecheck.
 * `tsc -b` rejected all seven of them, which is why `npm run build` failed
 * while `vite build` alone appeared to succeed — vite transpiles without
 * typechecking, so the broken build was invisible until the real gate ran.
 *
 * These helpers take the widened type and narrow it once, in a single place,
 * rather than scattering casts across the chart components.
 */

/**
 * A recharts tooltip value before narrowing.
 *
 * The array variant is `readonly`: recharts hands out its internal payload,
 * and a mutable element type here would make the whole formatter unassignable.
 */
export type TooltipValue =
  | number
  | string
  | readonly (number | string)[]
  | undefined;

/** Narrow a tooltip value to a number, or NaN when it is not one. */
export function toNumber(value: TooltipValue): number {
  if (typeof value === "number") return value;
  if (typeof value === "string") return Number(value);
  if (Array.isArray(value)) return toNumber(value[0]);
  return NaN;
}

/** Format a tooltip value as rupees, e.g. `₹1,23,456`. */
export function rupees(value: TooltipValue): string {
  const n = toNumber(value);
  return Number.isFinite(n) ? `₹${n.toLocaleString()}` : "—";
}

/** Format a tooltip value as a percentage to `digits` decimals. */
export function percent(value: TooltipValue, digits = 2): string {
  const n = toNumber(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : "—";
}
