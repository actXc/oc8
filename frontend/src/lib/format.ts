// Small shared formatting helpers with no natural home of their own -- kept
// here rather than duplicated per-route. `formatMs` started life local to
// `routes/agents.$id.tsx` (Agent KPIs & Statistics plan, Task 7) and moved
// here in Task 8 once the Department page needed the exact same formatting
// for the exact same KPI fields.

/** Renders `null`/`undefined` as "—" (still loading, or the backend has no
 * data for this window) and a real millisecond count as a compact human
 * duration -- "1.2s" under a minute, "3m 20s" under an hour, "1h 05m" above
 * that. */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  if (totalMinutes < 60) return `${totalMinutes}m ${seconds}s`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}
