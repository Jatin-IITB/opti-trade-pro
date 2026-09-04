import {
  AlertTriangle,
  BadgeCheck,
  BadgeX,
  FileSearch,
  Info,
  MinusCircle,
} from "lucide-react";

/**
 * Analyst reports, with a provenance verdict rendered beside every claim.
 *
 * The badge is the point of this panel, not decoration. Analyst output is
 * prose that asserts numbers, and it is the one surface in this app where a
 * real measurement and an invented one are typographically identical — the
 * failure mode four phases of work went into removing. So each claim shows
 * the journal sequences it cites and whether the auditor actually found its
 * numbers in those events. An ungrounded claim is shown, with the auditor's
 * reason, rather than dropped: hiding it would leave the surrounding
 * paragraph looking fully sourced.
 *
 * Partial coverage is normal, not an error. Each analyst reads one journal
 * event type and has nothing to say when the desk has not produced it yet, so
 * the panel lists who could not report and what each was missing. It also
 * lists the analyst that is deliberately not on the roster, so the reader is
 * never left to infer that this is everyone.
 */

interface Claim {
  claimId: string;
  statement: string;
  citations: number[];
  grounded: boolean;
  reasons: string[];
  matched: { name: string; sequence: number }[];
}

interface Report {
  name: string;
  title: string;
  requires: string;
  summary: string;
  claims: Claim[];
  claimsTotal: number;
  claimsGrounded: number;
  /** Null when the analyst made no numeric claims: nothing was audited. */
  groundedRate: number | null;
  auditSummary: string;
}

interface Failure {
  name: string;
  title: string;
  requires: string;
  reason: string;
}

interface Excluded {
  name: string;
  title: string;
  reason: string;
}

interface AnalystData {
  hasJournal?: boolean;
  reason?: string | null;
  runId?: string;
  eventsSeen?: number;
  /** The configured run id has no journal, but other run ids do. */
  runIdMismatch?: boolean;
  availableRunIds?: string[];
  groundedRate?: number | null;
  claimsTotal?: number;
  claimsGrounded?: number;
  analysts?: Report[];
  failures?: Failure[];
  excluded?: Excluded[];
  rosterSize?: number;
  warnings?: string[];
}

interface Props {
  data: AnalystData | null | undefined;
}

export function AnalystPanel({ data }: Props) {
  // Fails CLOSED: only an explicit `hasJournal: true` renders reports.
  //
  // Not `!== false`, which would let a missing key, a null payload, a schema
  // change or a backend exception all render the panel. With analyst prose
  // that would mean showing sentences with no journal behind them, which is
  // strictly worse than showing nothing.
  if (data?.hasJournal !== true) {
    return (
      <EmptyState
        reason={data?.reason}
        excluded={data?.excluded ?? []}
        mismatch={data?.runIdMismatch === true}
        configuredRunId={data?.runId}
        availableRunIds={data?.availableRunIds ?? []}
      />
    );
  }

  const reports = data.analysts ?? [];
  const failures = data.failures ?? [];
  const excluded = data.excluded ?? [];
  const claimsTotal = data.claimsTotal ?? 0;
  const claimsGrounded = data.claimsGrounded ?? 0;
  const rate = data.groundedRate;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Analysts</h2>
          <p className="mt-1 text-sm text-slate-400">
            Deterministic analysts reading the desk journal
            {data.runId ? ` "${data.runId}"` : ""} — {data.eventsSeen ?? 0}{" "}
            events. Every number is checked against the event it cites.
          </p>
        </div>
        <GroundedRate
          rate={rate}
          grounded={claimsGrounded}
          total={claimsTotal}
        />
      </header>

      {(data.warnings ?? []).map((w) => (
        <div
          key={w}
          className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{w}</span>
        </div>
      ))}

      <div className="flex flex-wrap gap-2 text-xs text-slate-400">
        <span className="rounded-full bg-slate-800 px-2.5 py-1">
          {reports.length} of {data.rosterSize ?? reports.length + failures.length}{" "}
          reported
        </span>
        {failures.length > 0 && (
          <span className="rounded-full bg-slate-800 px-2.5 py-1">
            {failures.length} had no facts to cite
          </span>
        )}
        {excluded.length > 0 && (
          <span className="rounded-full bg-slate-800 px-2.5 py-1">
            {excluded.length} not on this roster
          </span>
        )}
      </div>

      {reports.length === 0 && (
        <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-6 text-sm text-slate-400">
          No analyst could report on this journal yet. What each of them was
          waiting for is listed below.
        </div>
      )}

      {reports.map((report) => (
        <ReportCard key={report.name} report={report} />
      ))}

      {failures.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-medium text-slate-300">
            Could not report
          </h3>
          <p className="text-xs text-slate-500">
            Each analyst reads one kind of journal event. These are not errors
            — the desk has not written those events yet.
          </p>
          {failures.map((f) => (
            <div
              key={f.name}
              className="rounded-lg border border-slate-700 bg-slate-800/40 p-3"
            >
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
                <span className="text-sm font-medium text-slate-200">
                  {f.title}
                </span>
                {f.requires && (
                  <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-xs text-slate-400">
                    needs {f.requires}
                  </code>
                )}
              </div>
              <p className="mt-1.5 font-mono text-xs leading-relaxed text-slate-400">
                {f.reason}
              </p>
            </div>
          ))}
        </section>
      )}

      {excluded.length > 0 && <ExcludedList excluded={excluded} />}
    </div>
  );
}

function GroundedRate({
  rate,
  grounded,
  total,
}: {
  rate: number | null | undefined;
  grounded: number;
  total: number;
}) {
  // No claims means nothing was measured. A "100%" here would read as a
  // clean audit of something.
  if (rate == null || total === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-800/50 px-4 py-2.5 text-right">
        <div className="font-mono text-lg text-slate-500">—</div>
        <div className="text-xs text-slate-500">no claims audited</div>
      </div>
    );
  }
  const clean = rate >= 1;
  return (
    <div
      className={`rounded-lg border px-4 py-2.5 text-right ${
        clean
          ? "border-emerald-500/30 bg-emerald-500/10"
          : "border-rose-500/40 bg-rose-500/10"
      }`}
    >
      <div
        className={`font-mono text-lg ${
          clean ? "text-emerald-300" : "text-rose-300"
        }`}
      >
        {(rate * 100).toFixed(0)}%
      </div>
      <div className="text-xs text-slate-400">
        {grounded} of {total} claims grounded
      </div>
    </div>
  );
}

function ReportCard({ report }: { report: Report }) {
  // An analyst with no claims is NOT "all grounded". `claimsGrounded ===
  // claimsTotal` is trivially true at 0, which drew a green badge over prose
  // that cited nothing; a zero-claim report is unaudited, and neither green
  // nor red describes it.
  const audited = report.claimsTotal > 0;
  const allGrounded = audited && report.claimsGrounded === report.claimsTotal;
  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/50 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-base font-medium text-slate-100">
          {report.title}
        </h3>
        <div className="flex items-center gap-2">
          {report.requires && (
            <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-xs text-slate-500">
              {report.requires}
            </code>
          )}
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-medium ${
              !audited
                ? "bg-slate-700/50 text-slate-400"
                : allGrounded
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-rose-500/15 text-rose-300"
            }`}
          >
            {audited
              ? `${report.claimsGrounded}/${report.claimsTotal} grounded`
              : "no claims audited"}
          </span>
        </div>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-slate-300">
        {report.summary}
      </p>

      <div className="mt-4 space-y-2">
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Claims
        </h4>
        {report.claims.length === 0 && (
          <p className="text-sm text-slate-500">
            This analyst made no numeric claims.
          </p>
        )}
        {report.claims.map((claim) => (
          <ClaimRow key={claim.claimId} claim={claim} />
        ))}
      </div>
    </section>
  );
}

function ClaimRow({ claim }: { claim: Claim }) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        claim.grounded
          ? "border-slate-700 bg-slate-900/40"
          : "border-rose-500/40 bg-rose-500/5"
      }`}
    >
      <div className="flex items-start gap-2.5">
        {claim.grounded ? (
          <BadgeCheck
            className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400"
            aria-hidden
          />
        ) : (
          <BadgeX
            className="mt-0.5 h-4 w-4 shrink-0 text-rose-400"
            aria-hidden
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-relaxed text-slate-200">
            {claim.statement}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span
              className={
                claim.grounded
                  ? "font-medium text-emerald-400"
                  : "font-medium text-rose-400"
              }
            >
              {claim.grounded ? "Grounded" : "Ungrounded"}
            </span>
            <span className="text-slate-500">
              {claim.citations.length > 0 ? (
                <>
                  cites journal seq{" "}
                  <span className="font-mono text-slate-400">
                    {claim.citations.join(", ")}
                  </span>
                </>
              ) : (
                // A claim with no citations is ungrounded by definition;
                // saying so is more useful than an empty list.
                "cites nothing"
              )}
            </span>
          </div>
          {!claim.grounded && claim.reasons.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {claim.reasons.map((r) => (
                <li
                  key={r}
                  className="font-mono text-xs leading-relaxed text-rose-300"
                >
                  {r}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function ExcludedList({ excluded }: { excluded: Excluded[] }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium text-slate-300">Not on this roster</h3>
      {excluded.map((e) => (
        <div
          key={e.name}
          className="flex gap-2 rounded-lg border border-slate-700 bg-slate-800/30 p-3"
        >
          <MinusCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
          <div>
            <span className="text-sm font-medium text-slate-300">
              {e.title}
            </span>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              {e.reason}
            </p>
          </div>
        </div>
      ))}
    </section>
  );
}

function EmptyState({
  reason,
  excluded,
  mismatch,
  configuredRunId,
  availableRunIds,
}: {
  reason?: string | null;
  excluded: Excluded[];
  mismatch: boolean;
  configuredRunId?: string;
  availableRunIds: string[];
}) {
  // A misconfiguration and an idle desk both leave this panel empty, but only
  // one of them is fixed by waiting. Rendering them identically told users to
  // "run a desk cycle" when every cycle they ran wrote to a journal this
  // panel was not reading.
  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-slate-100">Analysts</h2>
      <div
        className={`rounded-xl border p-8 ${
          mismatch
            ? "border-amber-500/40 bg-amber-500/5"
            : "border-slate-700 bg-slate-800/50"
        }`}
      >
        <div className="mx-auto flex max-w-lg flex-col items-center text-center">
          <div
            className={`mb-4 rounded-full p-3 ${
              mismatch ? "bg-amber-500/15" : "bg-slate-700/50"
            }`}
          >
            {mismatch ? (
              <AlertTriangle className="h-6 w-6 text-amber-400" />
            ) : (
              <FileSearch className="h-6 w-6 text-slate-400" />
            )}
          </div>
          <h3 className="mb-2 text-base font-medium text-slate-200">
            {mismatch
              ? "This panel is reading the wrong journal"
              : "Nothing journaled to analyse yet"}
          </h3>
          <p className="text-sm leading-relaxed text-slate-400">
            {reason ??
              "Waiting for the first response from the server. The analysts read " +
                "the paper desk's event journal, so this panel fills in once a " +
                "desk cycle has run."}
          </p>

          {mismatch && (
            <dl className="mt-5 w-full space-y-1.5 rounded-lg bg-slate-900/50 p-3 text-left font-mono text-xs">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">configured run id</dt>
                <dd className="text-amber-300">{configuredRunId || "—"}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">journals on disk</dt>
                <dd className="text-slate-300">
                  {availableRunIds.join(", ") || "—"}
                </dd>
              </div>
            </dl>
          )}

          <p className="mt-6 text-xs leading-relaxed text-slate-500">
            {mismatch
              ? "No analyst text is shown because none of it would be about the desk you are running."
              : "No analyst text is shown rather than a sample report — prose asserting numbers with no journal behind it is indistinguishable from a real finding."}
          </p>
        </div>
      </div>
      {excluded.length > 0 && <ExcludedList excluded={excluded} />}
    </div>
  );
}
