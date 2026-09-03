import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { rupees, type TooltipValue } from "../lib/chartFormat";

interface Bucket {
  name: string;
  value: number;
  color: string;
}

interface Props {
  data: {
    date: string;
    previousDate?: string;
    totalPnl: number;
    buckets: Bucket[];
    explainedFraction?: number;
    coverage?: number;
    legsCompared?: number;
    legsChanged?: number;
    gammaMarkedAgainstRealizedVariance?: boolean;
  };
}

/**
 * The caveats that keep the headline honest.
 *
 * `explainedFraction` alone is misleading: it is the share of the *compared*
 * P&L that the buckets account for, and `coverage` is the share of the book
 * that was comparable at all. 98% explained of a fifth of the book is not a
 * 98% explanation. Legs that were traded during the period are excluded from
 * the attribution entirely — Greeks describe a held position, not the cash
 * flow of trading it — so `legsChanged` has to be visible too.
 */
function Caveats({ data }: Props) {
  const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
  const parts: string[] = [];
  if (data.explainedFraction !== undefined) {
    parts.push(`${pct(data.explainedFraction)} explained by the buckets`);
  }
  if (data.coverage !== undefined && data.legsCompared !== undefined) {
    parts.push(
      `covering ${pct(data.coverage)} of the book (${data.legsCompared} leg${
        data.legsCompared === 1 ? "" : "s"
      })`,
    );
  }
  if (data.legsChanged) {
    parts.push(
      `${data.legsChanged} leg${data.legsChanged === 1 ? "" : "s"} traded ` +
        `during the period and ${
          data.legsChanged === 1 ? "is" : "are"
        } excluded`,
    );
  }
  if (!parts.length) return null;

  const partial = (data.coverage ?? 1) < 0.999 || Boolean(data.legsChanged);
  return (
    <div
      className={`rounded-lg border px-4 py-3 text-xs leading-relaxed ${
        partial
          ? "border-amber-700/40 bg-amber-950/30 text-amber-200/80"
          : "border-slate-700/50 bg-slate-800/30 text-slate-400"
      }`}
    >
      {parts.join("; ")}.
      {data.gammaMarkedAgainstRealizedVariance === false &&
        " Gamma is marked against the single close-to-close move; no intraday path was recorded."}
    </div>
  );
}

export function PnlWaterfall({ data }: Props) {
  const chartData = data.buckets.map((b) => ({
    name: b.name,
    value: b.value,
    color: b.color,
    fill: b.value >= 0 ? "#22c55e" : "#ef4444",
  }));

  chartData.push({
    name: "Total",
    value: data.totalPnl,
    color: data.totalPnl >= 0 ? "#22c55e" : "#ef4444",
    fill: data.totalPnl >= 0 ? "#14b8a6" : "#f59e0b",
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            P&amp;L Attribution
          </h2>
          <p className="text-sm text-slate-400">
            Taylor decomposition: theta + delta + gamma-vs-RV + vega +
            vanna/volga + residual
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-400">
            {data.previousDate ? `${data.previousDate} → ${data.date}` : data.date}
          </div>
          <div
            className={`text-2xl font-mono ${
              data.totalPnl >= 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {data.totalPnl >= 0 ? "+" : ""}₹
            {data.totalPnl.toLocaleString()}
          </div>
        </div>
      </div>

      <Caveats data={data} />

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-6">
        <ResponsiveContainer width="100%" height={400}>
          <BarChart data={chartData} margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
            <XAxis
              dataKey="name"
              tick={{ fill: "#94a3b8", fontSize: 12 }}
              axisLine={{ stroke: "#334155" }}
              tickLine={false}
              angle={-30}
              textAnchor="end"
              height={60}
            />
            <YAxis
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              axisLine={{ stroke: "#334155" }}
              tickLine={false}
              tickFormatter={(v: number) => `₹${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                color: "#f1f5f9",
                fontSize: 13,
              }}
              formatter={(value: TooltipValue) => [rupees(value), "P&L"]}
            />
            <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {chartData.map((entry, i) => (
                <Cell key={i} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {data.buckets.map((b) => (
          <div
            key={b.name}
            className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3"
          >
            <div className="flex items-center gap-2">
              <div
                className="w-2 h-2 rounded-full"
                style={{ background: b.color }}
              />
              <span className="text-xs text-slate-400">{b.name}</span>
            </div>
            <div
              className={`text-sm font-mono mt-1 ${
                b.value >= 0 ? "text-emerald-400" : "text-red-400"
              }`}
            >
              {b.value >= 0 ? "+" : ""}₹{b.value.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
