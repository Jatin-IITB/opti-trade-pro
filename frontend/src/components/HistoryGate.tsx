import { Clock } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Shows a panel only when the backend says it has the history to fill it.
 *
 * The VRP, backtest and P&L-explain panels all need days of captured data
 * that a fresh install does not have. Before this gate existed they rendered
 * a bundled random walk instead, which was indistinguishable from a real
 * result. The backend now sends `hasHistory: false` plus a `reason` and a
 * day count, and this renders that state rather than a chart.
 *
 * Deliberately not a spinner: nothing is loading. The data will exist in a
 * few trading days, and saying so is more useful than implying a wait of
 * seconds.
 */
interface HistoryState {
  hasHistory?: boolean;
  reason?: string | null;
  daysAvailable?: number;
  daysRequired?: number;
}

interface Props {
  data: HistoryState | null | undefined;
  title: string;
  children: ReactNode;
}

export function HistoryGate({ data, title, children }: Props) {
  // Fails CLOSED: only an explicit `hasHistory: true` renders the panel.
  //
  // The earlier polarity (`hasHistory !== false`) let absent data through,
  // which meant a missing key, a null payload, a schema change or a backend
  // exception all rendered a chart. That was safe only while `demo.json`
  // carried plausible stand-in series for these three panels — and those
  // series were exactly the fabrication this phase removed. With no
  // fallback data left, "absent" now correctly means "nothing to show".
  if (data?.hasHistory !== true) {
    return (
      <EmptyState
        title={title}
        reason={data?.reason}
        available={data?.daysAvailable ?? 0}
        required={data?.daysRequired ?? 0}
      />
    );
  }
  return <>{children}</>;
}

function EmptyState({
  title,
  reason,
  available,
  required,
}: {
  title: string;
  reason?: string | null;
  available: number;
  required: number;
}) {
  const progress =
    required > 0 ? Math.min(100, (available / required) * 100) : 0;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-slate-100">{title}</h2>
      <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-8">
        <div className="mx-auto flex max-w-lg flex-col items-center text-center">
          <div className="mb-4 rounded-full bg-slate-700/50 p-3">
            <Clock className="h-6 w-6 text-slate-400" />
          </div>
          <h3 className="mb-2 text-base font-medium text-slate-200">
            Not enough history yet
          </h3>
          <p className="text-sm leading-relaxed text-slate-400">
            {reason ??
              "Waiting for the first data from the server. This panel needs " +
                "captured history, which accumulates once a capture schedule " +
                "is running."}
          </p>

          {required > 0 && (
            <div className="mt-6 w-full">
              <div className="mb-1.5 flex justify-between font-mono text-xs text-slate-500">
                <span>
                  {available} of {required} days
                </span>
                <span>{progress.toFixed(0)}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
                <div
                  className="h-full rounded-full bg-sky-500 transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          <p className="mt-6 text-xs leading-relaxed text-slate-500">
            Nothing is shown here rather than a placeholder curve — a simulated
            result on this panel would be indistinguishable from a real one.
          </p>
        </div>
      </div>
    </div>
  );
}
