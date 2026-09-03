import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  Play,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { HistoryGate } from "./HistoryGate";

/**
 * The paper-trading desk: cycle history, current book, decision trail and
 * the kill switch.
 *
 * Two things this panel must never do, both learned from the audit that
 * removed fabricated data from this app:
 *
 * 1. **Never render an empty table as a flat book.** A desk that has not run
 *    and a desk holding nothing look identical in a table with no rows and
 *    mean opposite things, so the cycle history sits behind `HistoryGate`.
 * 2. **Never let a paper fill read as a real one.** Every fill here is
 *    simulated inside the quant core against a stored snapshot. The badge is
 *    always visible, not tucked into a tooltip, and the payload's `isPaper`
 *    is asserted rather than assumed — if the backend ever stopped saying
 *    so, this panel says it cannot vouch for the fills.
 */

interface KillSwitchState {
  engaged?: boolean;
  reason?: string | null;
  path?: string;
  readable?: boolean;
}

interface CycleRow {
  date: string;
  action_taken: string;
  n_fills: number;
  n_rejected: number;
  equity: number;
  cash: number;
  drawdown: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  hedge_action: string | null;
  halted: boolean;
  correlation_id: string;
  fills: Array<Record<string, any>>;
  rejected: Array<Record<string, any>>;
}

interface DeskData {
  isPaper?: boolean;
  mode?: string;
  underlying?: string;
  killSwitch?: KillSwitchState;
  history?: {
    hasHistory?: boolean;
    reason?: string | null;
    daysAvailable?: number;
    daysRequired?: number;
  };
  cycles?: CycleRow[];
  book?: Array<Record<string, any>>;
  account?: Record<string, any> | null;
  warnings?: string[];
}

interface Props {
  data: DeskData | null | undefined;
}

const RESET_PHRASE = "RESET";

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function DeskPanel({ data }: Props) {
  const [killSwitch, setKillSwitch] = useState<KillSwitchState | undefined>(
    data?.killSwitch,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advanceNote, setAdvanceNote] = useState<string | null>(null);

  // The broadcast carries the switch state, but only as often as a capture
  // ticks. A control whose label can lag the thing it controls invites a
  // misclick, so the panel also polls the dedicated endpoint.
  const refreshKillSwitch = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/desk/kill-switch");
      if (resp.ok) setKillSwitch(await resp.json());
    } catch {
      /* leave the last known state; the badge shows what it last read */
    }
  }, []);

  useEffect(() => {
    refreshKillSwitch();
    const timer = setInterval(refreshKillSwitch, 10000);
    return () => clearInterval(timer);
  }, [refreshKillSwitch]);

  useEffect(() => {
    if (data?.killSwitch) setKillSwitch(data.killSwitch);
  }, [data?.killSwitch]);

  const engaged = killSwitch?.engaged === true;

  if (!data) {
    return (
      <div className="space-y-4">
        <DeskHeader engaged={engaged} isPaper={false} />
        <div className="rounded-xl border border-amber-700/50 bg-amber-950/20 p-6 text-sm text-amber-200">
          The server has not sent any desk state yet. Nothing is shown here
          rather than an empty book, which would read as a desk that has
          traded and holds nothing.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <DeskHeader engaged={engaged} isPaper={data.isPaper === true} />

      {data.isPaper !== true && (
        <div className="rounded-xl border border-red-700/60 bg-red-950/30 p-4 text-sm text-red-200">
          <strong className="font-semibold">
            This payload does not declare itself as paper.
          </strong>{" "}
          The fills below cannot be vouched for as simulated. Treat them as
          unverified and check the server build.
        </div>
      )}

      <KillSwitchCard
        state={killSwitch}
        busy={busy}
        error={error}
        onEngage={async (reason) => {
          setBusy(true);
          setError(null);
          try {
            const resp = await fetch("/api/v1/desk/kill-switch/engage", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ reason }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            setKillSwitch(await resp.json());
          } catch (e) {
            setError(`Could not halt the desk: ${String(e)}`);
          } finally {
            setBusy(false);
          }
        }}
        onReset={async (reason) => {
          setBusy(true);
          setError(null);
          try {
            const resp = await fetch("/api/v1/desk/kill-switch/reset", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ confirm: RESET_PHRASE, reason }),
            });
            if (!resp.ok) {
              const body = await resp.json().catch(() => ({}));
              throw new Error(body.detail ?? `HTTP ${resp.status}`);
            }
            setKillSwitch(await resp.json());
          } catch (e) {
            setError(`Could not clear the halt: ${String(e)}`);
          } finally {
            setBusy(false);
          }
        }}
      />

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy || engaged}
          onClick={async () => {
            setBusy(true);
            setError(null);
            setAdvanceNote(null);
            try {
              const resp = await fetch("/api/v1/desk/advance", {
                method: "POST",
              });
              if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
              const body = await resp.json();
              setAdvanceNote(
                (body.warnings ?? []).join(" · ") ||
                  "The desk advanced with nothing to report.",
              );
            } catch (e) {
              setError(`Advance failed: ${String(e)}`);
            } finally {
              setBusy(false);
            }
          }}
          className="inline-flex items-center gap-2 rounded-lg border border-sky-700 bg-sky-900/40 px-3 py-2 text-sm font-medium text-sky-200 hover:bg-sky-900/70 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Play className="h-4 w-4" />
          Run pending days (paper)
        </button>
        <span className="text-xs text-slate-500">
          {engaged
            ? "Disabled while the desk is halted."
            : "Runs one cycle per captured day not yet processed. Idempotent."}
        </span>
      </div>

      {advanceNote && (
        <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-3 font-mono text-xs text-slate-300">
          {advanceNote}
        </div>
      )}

      {data.account && <AccountStrip account={data.account} />}

      <HistoryGate data={data.history} title="Paper Desk — Cycle History">
        <div className="space-y-4">
          <BookTable book={data.book ?? []} />
          <CycleTable cycles={data.cycles ?? []} />
        </div>
      </HistoryGate>

      {(data.warnings ?? []).length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-800/30 p-3">
          <div className="mb-1 text-xs font-medium text-slate-400">Notes</div>
          <ul className="space-y-0.5 font-mono text-xs text-slate-500">
            {(data.warnings ?? []).map((w, i) => (
              <li key={i}>· {w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DeskHeader({
  engaged,
  isPaper,
}: {
  engaged: boolean;
  isPaper: boolean;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
          Paper Desk
          {isPaper && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-600/60 bg-amber-950/40 px-2 py-0.5 text-xs font-semibold tracking-wide text-amber-300 uppercase">
              <FlaskConical className="h-3 w-3" />
              Paper — no real orders
            </span>
          )}
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-400">
          Fills are simulated inside the quant core against captured
          end-of-day snapshots. This application has no order-placement path
          and its broker connection is read-only.
        </p>
      </div>
      <span
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${
          engaged
            ? "border-red-700 bg-red-950/40 text-red-300"
            : "border-emerald-800 bg-emerald-950/30 text-emerald-300"
        }`}
      >
        {engaged ? (
          <ShieldAlert className="h-3.5 w-3.5" />
        ) : (
          <ShieldCheck className="h-3.5 w-3.5" />
        )}
        {engaged ? "HALTED" : "Desk clear"}
      </span>
    </div>
  );
}

/**
 * The kill switch. Asymmetric on purpose: one click halts, while resuming
 * requires typing the confirmation phrase. Stopping is always safe; starting
 * again is the decision that deserves friction.
 */
function KillSwitchCard({
  state,
  busy,
  error,
  onEngage,
  onReset,
}: {
  state: KillSwitchState | undefined;
  busy: boolean;
  error: string | null;
  onEngage: (reason: string) => void;
  onReset: (reason: string) => void;
}) {
  const engaged = state?.engaged === true;
  const [confirm, setConfirm] = useState("");
  const [resetReason, setResetReason] = useState("");

  return (
    <div
      className={`rounded-xl border p-4 ${
        engaged
          ? "border-red-800/70 bg-red-950/20"
          : "border-slate-700 bg-slate-800/50"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-200">Kill switch</h3>
          <p className="mt-1 text-xs text-slate-400">
            {engaged
              ? "The desk is halted. It will place no orders of any kind until this is cleared."
              : "The desk may run. Engaging takes effect immediately and survives a restart."}
          </p>
          {state?.reason && (
            <p className="mt-2 font-mono text-xs break-words text-red-300">
              {state.reason}
            </p>
          )}
          {state?.readable === false && (
            <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              The latch could not be read, so the desk is reported as halted.
            </p>
          )}
          {state?.path && (
            <p className="mt-2 font-mono text-[11px] text-slate-500">
              {state.path}
            </p>
          )}
        </div>

        {!engaged && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onEngage("halted from the dashboard")}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-red-700 bg-red-900/40 px-4 py-2.5 text-sm font-semibold text-red-200 hover:bg-red-900/70 disabled:opacity-40"
          >
            <ShieldAlert className="h-4 w-4" />
            Halt the desk
          </button>
        )}
      </div>

      {engaged && (
        <div className="mt-4 space-y-2 border-t border-red-900/40 pt-4">
          <label className="block text-xs font-medium text-slate-300">
            To resume, type{" "}
            <span className="font-mono text-slate-100">{RESET_PHRASE}</span> and
            state a reason
          </label>
          <div className="flex flex-wrap gap-2">
            <input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={RESET_PHRASE}
              className="w-28 rounded-lg border border-slate-600 bg-slate-900 px-2 py-1.5 font-mono text-sm text-slate-100 placeholder:text-slate-600"
            />
            <input
              value={resetReason}
              onChange={(e) => setResetReason(e.target.value)}
              placeholder="Why is it safe to resume?"
              className="min-w-0 flex-1 rounded-lg border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 placeholder:text-slate-600"
            />
            <button
              type="button"
              disabled={
                busy || confirm !== RESET_PHRASE || resetReason.trim() === ""
              }
              onClick={() => {
                onReset(resetReason.trim());
                setConfirm("");
                setResetReason("");
              }}
              className="rounded-lg border border-emerald-700 bg-emerald-900/40 px-3 py-1.5 text-sm font-medium text-emerald-200 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-30"
            >
              Clear halt
            </button>
          </div>
          <p className="text-[11px] text-slate-500">
            The reason is written to the event journal, so a resume is
            auditable.
          </p>
        </div>
      )}

      {error && (
        <p className="mt-3 font-mono text-xs text-red-400">{error}</p>
      )}
    </div>
  );
}

function AccountStrip({ account }: { account: Record<string, any> }) {
  const cells: Array<[string, string]> = [
    ["Equity", fmt(account.equity)],
    ["Cash", fmt(account.cash)],
    ["High-water mark", fmt(account.highWaterMark)],
    ["Drawdown", `${fmt((account.drawdown ?? 0) * 100, 3)}%`],
    ["Gross notional", fmt(account.grossNotional)],
    ["Positions", String(account.nPositions ?? 0)],
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {cells.map(([label, value]) => (
        <div
          key={label}
          className="rounded-lg border border-slate-700 bg-slate-800/50 p-3"
        >
          <div className="text-[11px] tracking-wide text-slate-500 uppercase">
            {label}
          </div>
          <div className="mt-0.5 font-mono text-sm text-slate-100">{value}</div>
        </div>
      ))}
      <p className="col-span-full text-[11px] text-slate-500">
        A notional account funded at the configured starting equity — not your
        broker balance.
      </p>
    </div>
  );
}

function BookTable({ book }: { book: Array<Record<string, any>> }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-200">
        Current paper book
      </h3>
      {book.length === 0 ? (
        <p className="text-sm text-slate-400">
          The desk has run and currently holds no positions. This is a
          measured flat book, not an absence of data.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs tracking-wide text-slate-500 uppercase">
              <tr>
                <th className="pb-2 pr-4">Symbol</th>
                <th className="pb-2 pr-4 text-right">Strike</th>
                <th className="pb-2 pr-4">Type</th>
                <th className="pb-2 pr-4 text-right">Qty (lots)</th>
                <th className="pb-2 pr-4 text-right">Lot</th>
                <th className="pb-2 text-right">Entry</th>
              </tr>
            </thead>
            <tbody className="font-mono text-slate-200">
              {book.map((p, i) => (
                <tr key={i} className="border-t border-slate-700/60">
                  <td className="py-1.5 pr-4">{p.symbol}</td>
                  <td className="py-1.5 pr-4 text-right">{fmt(p.strike, 0)}</td>
                  <td className="py-1.5 pr-4 uppercase">{p.optionType}</td>
                  <td
                    className={`py-1.5 pr-4 text-right ${
                      p.quantity < 0 ? "text-red-300" : "text-emerald-300"
                    }`}
                  >
                    {fmt(p.quantity, 0)}
                  </td>
                  <td className="py-1.5 pr-4 text-right">{p.lotSize}</td>
                  <td className="py-1.5 text-right">{fmt(p.entryPrice)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CycleTable({ cycles }: { cycles: CycleRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const ordered = [...cycles].reverse();

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="mb-1 text-sm font-semibold text-slate-200">
        Cycle history
      </h3>
      <p className="mb-3 text-xs text-slate-500">
        Most recent first. Select a row for the decision trail: the market it
        saw, what the debate panel concluded, and which risk check bound.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs tracking-wide text-slate-500 uppercase">
            <tr>
              <th className="pb-2 pr-3"></th>
              <th className="pb-2 pr-4">Date</th>
              <th className="pb-2 pr-4">Outcome</th>
              <th className="pb-2 pr-4 text-right">Fills</th>
              <th className="pb-2 pr-4 text-right">Rejected</th>
              <th className="pb-2 pr-4 text-right">Equity</th>
              <th className="pb-2 pr-4 text-right">Delta</th>
              <th className="pb-2 pr-4 text-right">Vega</th>
              <th className="pb-2">Hedge</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((c) => (
              <CycleRowView
                key={c.correlation_id}
                cycle={c}
                open={open === c.correlation_id}
                onToggle={() =>
                  setOpen(open === c.correlation_id ? null : c.correlation_id)
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CycleRowView({
  cycle,
  open,
  onToggle,
}: {
  cycle: CycleRow;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-t border-slate-700/60 hover:bg-slate-700/20 ${
          cycle.halted ? "bg-red-950/20" : ""
        }`}
      >
        <td className="py-1.5 pr-3 text-slate-500">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </td>
        <td className="py-1.5 pr-4 font-mono text-slate-200">{cycle.date}</td>
        <td className="py-1.5 pr-4 text-slate-300">
          {cycle.halted && (
            <span className="mr-1.5 font-semibold text-red-400">HALT</span>
          )}
          {cycle.action_taken}
        </td>
        <td className="py-1.5 pr-4 text-right font-mono text-emerald-300">
          {cycle.n_fills}
        </td>
        <td className="py-1.5 pr-4 text-right font-mono text-amber-300">
          {cycle.n_rejected}
        </td>
        <td className="py-1.5 pr-4 text-right font-mono text-slate-200">
          {fmt(cycle.equity, 0)}
        </td>
        <td className="py-1.5 pr-4 text-right font-mono text-slate-400">
          {fmt(cycle.delta, 2)}
        </td>
        <td className="py-1.5 pr-4 text-right font-mono text-slate-400">
          {fmt(cycle.vega, 0)}
        </td>
        <td className="py-1.5 text-slate-400">{cycle.hedge_action ?? "—"}</td>
      </tr>
      {open && (
        <tr className="border-t border-slate-700/60 bg-slate-900/40">
          <td colSpan={9} className="p-4">
            <DecisionTrail correlationId={cycle.correlation_id} cycle={cycle} />
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Fetched lazily per cycle rather than shipped with the table: a trail
 * carries every expert opinion and every risk check, which is far more
 * payload than a year of summary rows.
 */
function DecisionTrail({
  correlationId,
  cycle,
}: {
  correlationId: string;
  cycle: CycleRow;
}) {
  const [trail, setTrail] = useState<any>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`/api/v1/desk/trail/${correlationId}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => live && setTrail(d))
      .catch((e) => live && setFailed(String(e)));
    return () => {
      live = false;
    };
  }, [correlationId]);

  if (failed) {
    return (
      <p className="font-mono text-xs text-red-400">
        The decision trail could not be loaded: {failed}
      </p>
    );
  }
  if (!trail) {
    return <p className="text-xs text-slate-500">Reading the journal…</p>;
  }
  if (trail.found !== true) {
    return (
      <p className="text-xs text-amber-300">
        {trail.reason ?? "No journal events carry this cycle's correlation id."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="font-mono text-[11px] text-slate-500">
        correlation {correlationId}
      </div>

      {cycle.rejected.length > 0 && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/20 p-3">
          <div className="mb-1 text-xs font-semibold text-amber-200">
            Orders not filled
          </div>
          <ul className="space-y-1 text-xs text-amber-100/90">
            {cycle.rejected.map((r, i) => (
              <li key={i}>
                <span className="font-mono">{r.symbol}</span> qty{" "}
                <span className="font-mono">{fmt(r.quantity, 0)}</span> —{" "}
                {r.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(trail.steps ?? []).map((step: any, i: number) => (
        <TrailStep key={i} step={step} />
      ))}
    </div>
  );
}

function TrailStep({ step }: { step: any }) {
  const shell =
    "rounded-lg border border-slate-700 bg-slate-800/40 p-3 text-xs";

  if (step.kind === "market") {
    return (
      <div className={shell}>
        <StepTitle>Market seen</StepTitle>
        <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-slate-300">
          <span>spot {fmt(step.spot)}</span>
          <span>realized vol {fmt(step.realizedVol, 4)}</span>
          {Object.entries(step.features ?? {}).map(([k, v]) => (
            <span key={k}>
              {k} {fmt(v as number, 4)}
            </span>
          ))}
        </div>
        {step.features?.rv_is_prior === 1 && (
          <p className="mt-1.5 text-amber-300/90">
            Realized vol is a neutral prior, not a measurement — too few
            stored days. The variance risk premium is therefore 0 by
            construction, so the strategy cannot enter on it.
          </p>
        )}
      </div>
    );
  }

  if (step.kind === "debate") {
    const approved = step.consensus === "approve";
    return (
      <div className={shell}>
        <StepTitle>
          Debate panel —{" "}
          <span className={approved ? "text-emerald-300" : "text-red-300"}>
            {String(step.consensus).toUpperCase()}
          </span>
        </StepTitle>
        <p className="mb-2 text-slate-300">{step.rationale}</p>
        <div className="space-y-1">
          {(step.opinions ?? []).map((op: any, i: number) => (
            <div key={i} className="flex flex-wrap gap-2 text-slate-400">
              <span className="w-36 shrink-0 font-mono text-slate-300">
                {op.expert}
              </span>
              <span
                className={`w-16 shrink-0 font-semibold ${
                  op.stance === "approve"
                    ? "text-emerald-400"
                    : op.stance === "reject"
                      ? "text-red-400"
                      : "text-slate-500"
                }`}
              >
                {String(op.stance).toUpperCase()}
              </span>
              <span className="w-12 shrink-0 font-mono">
                {fmt(op.confidence, 2)}
              </span>
              <span className="min-w-0 flex-1">{op.assessment}</span>
            </div>
          ))}
        </div>
        {(step.dissenters ?? []).length > 0 && (
          <p className="mt-2 text-amber-300">
            Dissenting: {(step.dissenters ?? []).join(", ")}
          </p>
        )}
      </div>
    );
  }

  if (step.kind === "risk") {
    const approved = step.verdict === "approve";
    return (
      <div className={shell}>
        <StepTitle>
          Risk review —{" "}
          <span className={approved ? "text-emerald-300" : "text-red-300"}>
            {String(step.verdict).toUpperCase()}
          </span>
        </StepTitle>
        {(step.bindingReasons ?? []).length > 0 && (
          <ul className="mb-2 space-y-0.5 text-red-300">
            {step.bindingReasons.map((r: string, i: number) => (
              <li key={i}>· {r}</li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-slate-400">
          {(step.checks ?? []).map((c: any, i: number) => (
            <span key={i}>
              {c.check}{" "}
              <span
                className={
                  c.verdict === "approve" ? "text-emerald-400" : "text-red-400"
                }
              >
                {c.verdict}
              </span>
            </span>
          ))}
        </div>
      </div>
    );
  }

  if (step.kind === "hedge") {
    return (
      <div className={shell}>
        <StepTitle>Delta hedge — {step.action}</StepTitle>
        <p className="text-slate-300">{step.rationale}</p>
        <p className="mt-1 text-slate-500">
          Hedge decisions are journaled, not booked: the paper loop tracks the
          option book plus the decision.
        </p>
      </div>
    );
  }

  if (step.kind === "rejection") {
    return (
      <div className={shell}>
        <StepTitle>
          Order rejected at the {step.stage} stage
        </StepTitle>
        <p className="text-amber-200">{step.reason}</p>
      </div>
    );
  }

  if (step.kind === "halt") {
    return (
      <div className="rounded-lg border border-red-800 bg-red-950/30 p-3 text-xs">
        <StepTitle>Kill switch engaged</StepTitle>
        <p className="text-red-200">{step.reason}</p>
        <p className="mt-1 text-red-300/80">
          {step.cancelledOrders} order(s) cancelled unfilled. The desk placed
          nothing further and skipped the hedge.
        </p>
      </div>
    );
  }

  return null;
}

function StepTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-xs font-semibold text-slate-200">
      {children}
    </div>
  );
}
