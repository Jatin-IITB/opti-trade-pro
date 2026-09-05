import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  LineChart,
  Line,
  Legend,
} from "recharts";
import { percent, toNumber, type TooltipValue } from "../lib/chartFormat";

/**
 * Nullable fields have no honest source yet and must not render as zero:
 * `drawdown` needs an equity high-water mark, `utilizationHistory` needs a
 * persisted time series, and `verdicts` needs the pre-trade engine to be
 * reviewing real orders (this app is read-only). Zero would read as
 * "no drawdown, no rejections" — a claim, not an absence.
 */
interface Props {
  data: {
    limits: Record<string, number>;
    current: Record<string, number> | null;
    marginUtilization?: number | null;
    drawdown?: number | null;
    utilizationHistory: {
      day: number;
      deltaUtil: number;
      gammaUtil: number;
      vegaUtil: number;
      drawdownUtil: number;
    }[];
    verdicts: Record<string, number> | null;
    legsPriced?: number;
    legsExcluded?: number;
    hasBook?: boolean;
  };
}

const UTIL_COLORS: Record<string, string> = {
  delta: "#3b82f6",
  gamma: "#f59e0b",
  vega: "#8b5cf6",
  drawdown: "#ef4444",
};

const VERDICT_COLORS: Record<string, string> = {
  APPROVE: "#22c55e",
  RESIZE: "#f59e0b",
  REJECT: "#ef4444",
  HALT: "#dc2626",
};

function utilPercent(current: number, limit: number): number {
  return (current / limit) * 100;
}

function utilColor(pct: number): string {
  if (pct >= 90) return "#ef4444";
  if (pct >= 70) return "#f59e0b";
  return "#22c55e";
}

export function RiskDashboard({ data }: Props) {
  const current = data.current;
  const gauges = [
    { key: "delta", label: "Delta", unit: "" },
    { key: "gamma", label: "Gamma", unit: "" },
    { key: "vega", label: "Vega", unit: "" },
  ].filter((g) => current && current[g.key] !== undefined);

  const verdicts = data.verdicts;
  const verdictData = verdicts
    ? Object.entries(verdicts).map(([name, count]) => ({
        name,
        count,
        fill: VERDICT_COLORS[name] || "#64748b",
      }))
    : [];

  const totalVerdicts = verdicts
    ? Object.values(verdicts).reduce((s, v) => s + v, 0)
    : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Risk Dashboard
          </h2>
          <p className="text-sm text-slate-400">
            Fail-closed pre-trade engine &mdash; Greeks caps, margin, drawdown
            halt, concentration resize (ADR-008)
          </p>
        </div>
        <div
          className={`px-3 py-1.5 rounded-lg border ${
            data.hasBook
              ? "bg-emerald-900/30 border-emerald-700/50"
              : "bg-slate-800/60 border-slate-700"
          }`}
        >
          <span
            className={`text-xs font-mono ${
              data.hasBook ? "text-emerald-400" : "text-slate-400"
            }`}
          >
            {data.hasBook
              ? `${data.legsPriced} legs priced`
              : "No book synced"}
          </span>
        </div>
      </div>

      {!current && (
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-6 text-center">
          <div className="text-amber-400 text-sm mb-1">
            No book exposure to report
          </div>
          <p className="text-slate-400 text-xs">
            Limit utilisation is computed from your synced F&amp;O positions at
            the live spot. Connect Upstox and let a capture cycle run.
          </p>
        </div>
      )}

      <div className="grid grid-cols-4 gap-3">
        {current &&
          gauges.map(({ key, label, unit }) => {
          const currentValue = current[key];
          const limit = data.limits[key];
          const pct = utilPercent(Math.abs(currentValue), limit);
          const displayCurrent =
            Math.abs(currentValue) < 0.1
              ? currentValue.toFixed(5)
              : currentValue.toLocaleString("en-IN", {
                  maximumFractionDigits: 2,
                });
          const displayLimit =
            limit < 0.1 ? limit.toFixed(3) : limit.toLocaleString("en-IN");

          return (
            <div
              key={key}
              className="bg-slate-800/50 rounded-lg border border-slate-700 p-4"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-slate-300">
                  {label}
                  {unit}
                </span>
                <span
                  className="text-xs font-mono px-1.5 py-0.5 rounded"
                  style={{
                    color: utilColor(pct),
                    background: `${utilColor(pct)}15`,
                  }}
                >
                  {pct.toFixed(0)}%
                </span>
              </div>
              <div className="w-full bg-slate-700 rounded-full h-2 mb-2">
                <div
                  className="h-2 rounded-full transition-all"
                  style={{
                    width: `${Math.min(pct, 100)}%`,
                    background: utilColor(pct),
                  }}
                />
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-400 font-mono">
                  {displayCurrent}
                </span>
                <span className="text-slate-600 font-mono">
                  / {displayLimit}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
          <div className="text-xs text-slate-500 mb-2">
            Utilization History (60d)
          </div>
          {data.utilizationHistory.length === 0 ? (
            <div className="h-[240px] flex items-center justify-center text-center px-6">
              <p className="text-slate-500 text-xs">
                No history recorded. Utilisation is computed live but not yet
                persisted, so there is no series to plot.
              </p>
            </div>
          ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data.utilizationHistory}>
              <XAxis
                dataKey="day"
                tick={{ fill: "#64748b", fontSize: 10 }}
                axisLine={{ stroke: "#334155" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: "#64748b", fontSize: 10 }}
                axisLine={{ stroke: "#334155" }}
                tickLine={false}
                tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                domain={[0, 1]}
              />
              <Tooltip
                contentStyle={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 8,
                  color: "#f1f5f9",
                  fontSize: 11,
                }}
                formatter={(value: TooltipValue, name) => [
                  percent(toNumber(value) * 100, 1),
                  String(name).replace("Util", ""),
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: 10, color: "#94a3b8" }}
                formatter={(value: string) => value.replace("Util", "")}
              />
              <Line
                type="monotone"
                dataKey="deltaUtil"
                stroke={UTIL_COLORS.delta}
                strokeWidth={1.5}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="gammaUtil"
                stroke={UTIL_COLORS.gamma}
                strokeWidth={1.5}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="vegaUtil"
                stroke={UTIL_COLORS.vega}
                strokeWidth={1.5}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="drawdownUtil"
                stroke={UTIL_COLORS.drawdown}
                strokeWidth={1.5}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
          )}
        </div>

        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
          <div className="text-xs text-slate-500 mb-2">
            Risk Verdicts{verdicts ? ` (${totalVerdicts} total)` : ""}
          </div>
          {!verdicts ? (
            <div className="h-[240px] flex items-center justify-center text-center px-6">
              <p className="text-slate-500 text-xs">
                No verdicts to show. The pre-trade engine reviews orders, and
                this app is read-only &mdash; it places none.
              </p>
            </div>
          ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={verdictData} layout="vertical">
              <XAxis
                type="number"
                tick={{ fill: "#64748b", fontSize: 10 }}
                axisLine={{ stroke: "#334155" }}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fill: "#94a3b8", fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={70}
              />
              <Tooltip
                contentStyle={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 8,
                  color: "#f1f5f9",
                  fontSize: 12,
                }}
                formatter={(value: TooltipValue) => [toNumber(value), "Count"]}
              />
              <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={24}>
                {verdictData.map((entry) => (
                  <Cell key={entry.name} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="bg-slate-800/20 rounded-lg border border-slate-700/30 p-4 text-xs text-slate-500">
        <strong className="text-slate-400">About the risk engine:</strong> any
        exception inside a check converts to REJECT, never a pass-through, and{" "}
        <code>test_risk.py</code> property-tests that no limit-breaching order is
        ever approved. Verdict precedence: HALT &gt; REJECT &gt; RESIZE &gt;
        APPROVE (ADR-008). Exposure above is computed from your synced book;
        drawdown, utilisation history and verdicts have no persisted source yet
        and are reported as absent rather than zero.
      </div>
    </div>
  );
}
