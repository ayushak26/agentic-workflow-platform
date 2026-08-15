/** Plain formatting helpers shared across the Business View's components.
 *
 * Kept in their own module (rather than alongside a component) so files that
 * export React components only export components — the fast-refresh rule
 * this project lints for.
 */
export function formatCost(usd: number): string {
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}
