import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  ComposedChart,
} from "recharts";

interface Props {
  data: {
    iv: number[];
    rv: number[];
    spread: number[];
    regimes: string[];
    nDays: number;
    meanSpread: number;
    entryThreshold: number;
    exitThreshold: number;
  };
}

const REGIME_COLORS: Record<string, string> = {
  rich: "#22c55e",
  cheap: "#ef4444",
  neutral: "#64748b",
};

export function VrpSignal({ data }: Props) {
  const chartData = data.iv.map((_, i) => ({
    day: i + 1,
    iv: data.iv[i] * 100,
    rv: data.rv[i] * 100,
    spread: data.spread[i] * 100,
    regime: data.regimes[i],
  }));

  const regimeCounts = data.regimes.reduce(
    (acc, r) => {
      acc[r] = (acc[r] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            VRP Signal Monitor
          </h2>
          <p className="text-sm text-slate-400">
            Variance Risk Premium (IV &minus; RV) &mdash; {data.nDays}-day
            lookback with regime detection
          </p>
        </div>
        <div className="flex items-center gap-2">
          {Object.entries(regimeCounts).map(([regime, count]) => (
            <div
              key={regime}
              className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 border border-slate-700"
            >
              <div
                className="w-2 h-2 rounded-full"
                style={{ background: REGIME_COLORS[regime] }}
              />
              <span className="text-xs text-slate-400">
                {regime}: {count}d
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <div className="text-xs text-slate-500 mb-2">
          Implied Vol vs Realized Vol
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={chartData}>
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
              tickFormatter={(v: number) => `${v.toFixed(0)}%`}
              domain={["dataMin - 1", "dataMax + 1"]}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                color: "#f1f5f9",
                fontSize: 12,
              }}
              formatter={(value: number, name: string) => [
                `${value.toFixed(2)}%`,
                name === "iv" ? "Implied Vol" : "Realized Vol",
              ]}
            />
            <Line
              type="monotone"
              dataKey="iv"
              stroke="#f59e0b"
              strokeWidth={2}
              dot={false}
              name="iv"
            />
            <Line
              type="monotone"
              dataKey="rv"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              name="rv"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <div className="text-xs text-slate-500 mb-2">
          VRP Spread (IV &minus; RV) with entry/exit thresholds
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={chartData}>
            <defs>
              <linearGradient id="spreadGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
                <stop offset="50%" stopColor="#22c55e" stopOpacity={0} />
                <stop offset="50%" stopColor="#ef4444" stopOpacity={0} />
                <stop offset="100%" stopColor="#ef4444" stopOpacity={0.3} />
              </linearGradient>
            </defs>
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
              tickFormatter={(v: number) => `${v.toFixed(1)}%`}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                color: "#f1f5f9",
                fontSize: 12,
              }}
              formatter={(value: number) => [`${value.toFixed(2)}%`, "Spread"]}
            />
            <ReferenceLine
              y={data.entryThreshold * 100}
              stroke="#22c55e"
              strokeDasharray="4 4"
              label={{
                value: "Entry",
                fill: "#22c55e",
                fontSize: 10,
                position: "right",
              }}
            />
            <ReferenceLine
              y={data.exitThreshold * 100}
              stroke="#ef4444"
              strokeDasharray="4 4"
              label={{
                value: "Exit",
                fill: "#ef4444",
                fontSize: 10,
                position: "right",
              }}
            />
            <ReferenceLine y={0} stroke="#475569" />
            <Area
              type="monotone"
              dataKey="spread"
              stroke="none"
              fill="url(#spreadGrad)"
            />
            <Line
              type="monotone"
              dataKey="spread"
              stroke="#94a3b8"
              strokeWidth={1.5}
              dot={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-xs text-slate-500">Mean Spread</div>
          <div
            className={`text-xl font-mono mt-1 ${
              data.meanSpread > 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {(data.meanSpread * 100).toFixed(2)}%
          </div>
          <div className="text-xs text-slate-600 mt-1">
            Positive = IV persistently overpriced
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-xs text-slate-500">Entry Threshold</div>
          <div className="text-xl font-mono text-emerald-400 mt-1">
            {(data.entryThreshold * 100).toFixed(1)}%
          </div>
          <div className="text-xs text-slate-600 mt-1">
            Sell vol when spread exceeds this
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-xs text-slate-500">Exit Threshold</div>
          <div className="text-xl font-mono text-red-400 mt-1">
            {(data.exitThreshold * 100).toFixed(1)}%
          </div>
          <div className="text-xs text-slate-600 mt-1">
            Close when premium collapses
          </div>
        </div>
      </div>
    </div>
  );
}
