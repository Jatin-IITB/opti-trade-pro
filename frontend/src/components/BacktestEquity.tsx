import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  BarChart,
  Bar,
  Cell,
} from "recharts";

interface Fold {
  fold: number;
  trainSharpe: number;
  testSharpe: number;
  startDay: number;
  endDay: number;
}

interface Props {
  data: {
    equity: number[];
    dailyPnl: number[];
    drawdown: number[];
    sharpe: number;
    deflatedSharpe: number;
    maxDrawdown: number;
    totalCosts: number;
    nTrades: number;
    initialEquity: number;
    nDays: number;
    folds: Fold[];
  };
}

export function BacktestEquity({ data }: Props) {
  const equityData = data.equity.map((e, i) => ({
    day: i,
    equity: e,
    drawdown: data.drawdown[i] * -100,
  }));

  const pnlData = data.dailyPnl.map((p, i) => ({
    day: i + 1,
    pnl: p,
    fill: p >= 0 ? "#22c55e" : "#ef4444",
  }));

  const totalReturn =
    ((data.equity[data.equity.length - 1] - data.initialEquity) /
      data.initialEquity) *
    100;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Walk-Forward Backtest
          </h2>
          <p className="text-sm text-slate-400">
            {data.nDays}-day OOS equity curve with {data.folds.length}-fold
            cross-validation and deflated Sharpe
          </p>
        </div>
        <div className="text-right">
          <div
            className={`text-2xl font-mono ${
              totalReturn >= 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {totalReturn >= 0 ? "+" : ""}
            {totalReturn.toFixed(1)}%
          </div>
          <div className="text-xs text-slate-500">total return</div>
        </div>
      </div>

      <div className="grid grid-cols-5 gap-3">
        {(
          [
            ["Sharpe", data.sharpe.toFixed(2), "annualized"],
            [
              "Deflated Sharpe",
              data.deflatedSharpe.toFixed(2),
              "trial-adjusted",
            ],
            [
              "Max Drawdown",
              `${(data.maxDrawdown * 100).toFixed(1)}%`,
              "peak-to-trough",
            ],
            [
              "Total Costs",
              `₹${(data.totalCosts / 1000).toFixed(1)}k`,
              "all-in",
            ],
            ["Trades", String(data.nTrades), "option fills"],
          ] as [string, string, string][]
        ).map(([label, value, sub]) => (
          <div
            key={label}
            className="bg-slate-800/50 rounded-lg border border-slate-700 p-3 text-center"
          >
            <div className="text-xs text-slate-500">{label}</div>
            <div className="text-lg font-mono text-slate-100 mt-1">{value}</div>
            <div className="text-[10px] text-slate-600">{sub}</div>
          </div>
        ))}
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <div className="text-xs text-slate-500 mb-2">Equity Curve</div>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={equityData}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="day"
              tick={{ fill: "#64748b", fontSize: 10 }}
              axisLine={{ stroke: "#334155" }}
              tickLine={false}
              label={{
                value: "Trading Day",
                position: "insideBottom",
                offset: -5,
                fill: "#64748b",
                fontSize: 11,
              }}
            />
            <YAxis
              tick={{ fill: "#64748b", fontSize: 10 }}
              axisLine={{ stroke: "#334155" }}
              tickLine={false}
              tickFormatter={(v: number) => `₹${(v / 1e6).toFixed(2)}M`}
              domain={["dataMin - 20000", "dataMax + 20000"]}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                color: "#f1f5f9",
                fontSize: 12,
              }}
              formatter={(value: number) => [
                `₹${value.toLocaleString()}`,
                "Equity",
              ]}
            />
            <ReferenceLine
              y={data.initialEquity}
              stroke="#475569"
              strokeDasharray="3 3"
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="#3b82f6"
              fill="url(#equityGrad)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <div className="text-xs text-slate-500 mb-2">Daily P&amp;L</div>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={pnlData}>
            <XAxis
              dataKey="day"
              tick={false}
              axisLine={{ stroke: "#334155" }}
            />
            <YAxis
              tick={{ fill: "#64748b", fontSize: 10 }}
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
                fontSize: 12,
              }}
              formatter={(value: number) => [
                `₹${value.toLocaleString()}`,
                "P&L",
              ]}
            />
            <ReferenceLine y={0} stroke="#475569" />
            <Bar dataKey="pnl" radius={[1, 1, 0, 0]}>
              {pnlData.map((entry, i) => (
                <Cell key={`pnl-${i}`} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div>
        <div className="text-sm font-medium text-slate-300 mb-2">
          Walk-Forward Folds
        </div>
        <div className="grid grid-cols-4 gap-3">
          {data.folds.map((f) => (
            <div
              key={f.fold}
              className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3"
            >
              <div className="text-xs text-slate-500">Fold {f.fold}</div>
              <div className="flex justify-between mt-2">
                <div>
                  <div className="text-[10px] text-slate-600">Train</div>
                  <div className="text-sm font-mono text-amber-400">
                    {f.trainSharpe.toFixed(2)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] text-slate-600">Test (OOS)</div>
                  <div
                    className={`text-sm font-mono ${
                      f.testSharpe > 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {f.testSharpe.toFixed(2)}
                  </div>
                </div>
              </div>
              <div className="text-[10px] text-slate-600 mt-1">
                Days {f.startDay}&ndash;{f.endDay}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
