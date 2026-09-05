import { Clock } from "lucide-react";

/**
 * States which session's prices the market panels are showing.
 *
 * The server holds its dashboard payload in memory only, so restarting it
 * discards the session's captures even though the Parquet snapshots survive on
 * disk. Rather than show nothing until the next trading day — which outside
 * market hours means most of the week — the server warm-starts from the newest
 * stored capture and flags it `isLive: false`.
 *
 * Deliberately understated. A closed market is the normal state for most of
 * the week, not a fault, so this reads as a dateline rather than a warning:
 * amber-alarm styling for "it is Saturday" trains the eye to ignore the strip
 * entirely, which is worse than not having it. It still cannot be missed,
 * because the alternative — a chain from Friday rendered exactly like one from
 * this second — is the failure being prevented.
 *
 * The sentence is composed server-side and rendered verbatim: only the server
 * knows the exchange calendar, so only it can tell a session's final capture
 * from one taken an hour before the close.
 */
interface Props {
  /** Backend verdict. When true this renders nothing. */
  marketIsLive: boolean;
  /** The server's description of the instant, shown as given. */
  note: string | null;
}

export function SnapshotNotice({ marketIsLive, note }: Props) {
  if (marketIsLive || note === null) return null;

  return (
    <div
      role="status"
      className="mb-4 flex items-center gap-2.5 rounded-lg border border-slate-600/50 bg-slate-800/40 px-4 py-2.5"
    >
      <Clock className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
      <p className="text-sm text-slate-300">{note}</p>
    </div>
  );
}
