import { Radio } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Shows a market panel only once a real captured chain has arrived.
 *
 * The market panels used to fall back to `demo.json`, a bundled chain priced
 * off a 20,000 NIFTY. Until the first capture completes the backend sends no
 * `dashboard_update` at all, so that fallback was what a user actually saw —
 * a fabricated surface, chain and Greeks rendered exactly like live ones,
 * with no badge to say otherwise. (The badge that once disclosed it, the
 * `DataSource` "Sim" marker, had been removed as unused.)
 *
 * This is the same contract `HistoryGate` applies to the replay-backed
 * panels, and for the same reason: a panel with no real data reports that
 * fact rather than showing a stand-in. The distinction is only *what* is
 * missing — `HistoryGate` waits for days of stored history, this waits for
 * the first live chain — so the wording differs and the components stay
 * separate.
 *
 * Deliberately not a spinner. Outside market hours nothing is loading and
 * none will arrive until the session opens; saying so beats implying a wait
 * of seconds.
 */
interface Props {
  data: unknown;
  title: string;
  /** Connection state, so a closed socket reads differently from a quiet one. */
  status?: "disconnected" | "connecting" | "connected" | "error";
  children: ReactNode;
}

export function LiveGate({ data, title, status, children }: Props) {
  // Fails closed, matching HistoryGate: only present data renders the panel.
  // Absent, null, a schema change or a backend exception all land here, which
  // is safe now that there is no bundled chain left to fall back to.
  if (data === null || data === undefined) {
    return <EmptyState title={title} status={status} />;
  }
  return <>{children}</>;
}

function reasonFor(status: Props["status"]): string {
  switch (status) {
    case "error":
    case "disconnected":
      return (
        "Not connected to the server, so no live chain can arrive. The " +
        "dashboard reconnects automatically; if this persists, check that " +
        "the API is running."
      );
    case "connecting":
      return "Connecting to the server and waiting for the first captured chain.";
    default:
      return (
        "Connected, but no option chain has been captured yet. The capture " +
        "scheduler runs inside NSE market hours, so this panel fills in on " +
        "the next capture after the session opens."
      );
  }
}

function EmptyState({
  title,
  status,
}: {
  title: string;
  status: Props["status"];
}) {
  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-slate-100">{title}</h2>
      <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-8">
        <div className="mx-auto flex max-w-lg flex-col items-center text-center">
          <div className="mb-4 rounded-full bg-slate-700/50 p-3">
            <Radio className="h-6 w-6 text-slate-400" />
          </div>
          <h3 className="mb-2 text-base font-medium text-slate-200">
            Waiting for the first captured chain
          </h3>
          <p className="text-sm leading-relaxed text-slate-400">
            {reasonFor(status)}
          </p>
          <p className="mt-6 text-xs leading-relaxed text-slate-500">
            Nothing is shown here rather than a sample chain — a simulated
            surface on this panel would be indistinguishable from a real one.
          </p>
        </div>
      </div>
    </div>
  );
}
