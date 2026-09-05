import Plot from "react-plotly.js";

interface Props {
  data: {
    strikes: number[];
    expiries: number[];
    ivs: number[][];
    spot: number;
  };
}

export function VolSurface({ data }: Props) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Implied Volatility Surface
          </h2>
          <p className="text-sm text-slate-400">
            Strike &times; Expiry &times; IV &mdash; fitted from the captured
            option chain
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>
            {data.strikes.length} strikes &times; {data.expiries.length} expiries
          </div>
          <div className="text-slate-500">
            ATM spot: {data.spot.toLocaleString()}
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <Plot
          data={[
            {
              type: "surface",
              x: data.strikes,
              y: data.expiries,
              z: data.ivs,
              colorscale: [
                [0, "#0d9488"],
                [0.25, "#2dd4bf"],
                [0.5, "#fbbf24"],
                [0.75, "#f97316"],
                [1, "#ef4444"],
              ],
              colorbar: {
                title: { text: "IV", font: { color: "#94a3b8", size: 12 } },
                tickfont: { color: "#94a3b8", size: 10 },
                tickformat: ".0%",
                len: 0.6,
              },
              hovertemplate:
                "Strike: %{x:,.0f}<br>Expiry: %{y:.2f}y<br>IV: %{z:.1%}<extra></extra>",
              contours: {
                z: { show: true, usecolormap: true, project: { z: true } },
              },
            },
          ]}
          layout={{
            autosize: true,
            height: 520,
            margin: { l: 10, r: 10, t: 30, b: 10 },
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            scene: {
              xaxis: {
                title: { text: "Strike", font: { color: "#94a3b8", size: 12 } },
                gridcolor: "#334155",
                zerolinecolor: "#334155",
                color: "#94a3b8",
                tickformat: ",",
              },
              yaxis: {
                title: { text: "Expiry (yr)", font: { color: "#94a3b8", size: 12 } },
                gridcolor: "#334155",
                zerolinecolor: "#334155",
                color: "#94a3b8",
              },
              zaxis: {
                title: { text: "IV", font: { color: "#94a3b8", size: 12 } },
                gridcolor: "#334155",
                zerolinecolor: "#334155",
                color: "#94a3b8",
                tickformat: ".0%",
              },
              bgcolor: "transparent",
              camera: { eye: { x: 1.8, y: -1.5, z: 0.8 } },
            },
            font: { color: "#94a3b8" },
          }}
          config={{ responsive: true, displayModeBar: true }}
          className="w-full"
        />
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          {
            label: "Skew",
            desc: "OTM puts trade richer — demand for downside protection",
          },
          {
            label: "Term structure",
            desc: "Longer expiries carry higher base vol — mean-reversion premium",
          },
          {
            label: "Smile wings",
            desc: "Deep OTM strikes show elevated IV — fat-tail pricing",
          },
        ].map(({ label, desc }) => (
          <div
            key={label}
            className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3"
          >
            <div className="text-sm font-medium text-slate-300">{label}</div>
            <div className="text-xs text-slate-500 mt-1">{desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
