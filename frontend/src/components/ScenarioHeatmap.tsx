import Plot from "react-plotly.js";

interface Extreme {
  pnl: number;
  spotShiftPct: number;
  volShiftPct: number;
}

/**
 * Axes are in percent and `pnl` is indexed [volIndex][spotIndex] — Plotly's
 * heatmap `z` is [y][x]. The backend transposes the engine's (spot, vol) cube
 * to match.
 *
 * `legsPriced` present means the grid was revalued over the user's real book;
 * `strike`/`expiry` present means it is the bundled demo reference contract.
 */
interface ScenarioData {
  spotShifts: number[];
  volShifts: number[];
  pnl: number[][];
  basePrice?: number;
  baseValue?: number;
  strike?: number;
  expiry?: number;
  legsPriced?: number;
  legsExcluded?: number;
  worst?: Extreme;
  best?: Extreme;
}

interface Props {
  data: ScenarioData | null;
}

function describe(e: Extreme): string {
  const spot = `${e.spotShiftPct >= 0 ? "+" : ""}${e.spotShiftPct.toFixed(1)}% spot`;
  const vol = `${e.volShiftPct >= 0 ? "+" : ""}${e.volShiftPct.toFixed(1)} vol pts`;
  return `${spot}, ${vol}`;
}

export function ScenarioHeatmap({ data }: Props) {
  if (!data) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-slate-100">
          Scenario P&amp;L Heatmap
        </h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
          <div className="text-amber-400 text-sm mb-2">No positions to revalue</div>
          <p className="text-slate-400 text-xs">
            This grid reprices your actual book across spot and volatility
            shifts. Connect Upstox and sync a position to populate it.
          </p>
        </div>
      </div>
    );
  }

  const isRealBook = data.legsPriced !== undefined;
  const flat = data.pnl.flat();
  const maxAbs = Math.max(...flat.map((v) => Math.abs(v)));
  const baseValue = data.baseValue ?? data.basePrice ?? 0;
  const best = data.best ?? null;
  const worst = data.worst ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Scenario P&amp;L Heatmap
          </h2>
          <p className="text-sm text-slate-400">
            {data.spotShifts.length} &times; {data.volShifts.length} grid &mdash;{" "}
            {isRealBook
              ? `spot shift vs vol shift across ${data.legsPriced} of your F&O legs`
              : `spot shift vs vol shift for a ${data.strike?.toLocaleString()} call, ${((data.expiry ?? 0) * 365).toFixed(0)}d expiry`}
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>
            Base value: &#8377;{Math.round(baseValue).toLocaleString("en-IN")}
          </div>
          <div className="text-slate-500">
            Full revaluation, not Greeks approximation
          </div>
          {isRealBook && (data.legsExcluded ?? 0) > 0 && (
            <div className="text-amber-500/80 mt-0.5">
              {data.legsExcluded} leg(s) unpriceable, excluded
            </div>
          )}
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <Plot
          data={[
            {
              type: "heatmap",
              x: data.spotShifts,
              y: data.volShifts,
              z: data.pnl,
              colorscale: [
                [0, "#ef4444"],
                [0.35, "#f87171"],
                [0.5, "#1e293b"],
                [0.65, "#4ade80"],
                [1, "#22c55e"],
              ],
              zmin: -maxAbs,
              zmax: maxAbs,
              colorbar: {
                title: { text: "P&L", font: { color: "#94a3b8", size: 12 } },
                tickfont: { color: "#94a3b8", size: 10 },
                tickprefix: "₹",
                len: 0.8,
              },
              hovertemplate:
                "Spot: %{x:+.1f}%<br>Vol: %{y:+.1f}%<br>P&L: ₹%{z:,.0f}<extra></extra>",
            },
          ]}
          layout={{
            autosize: true,
            height: 480,
            margin: { l: 60, r: 20, t: 20, b: 50 },
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            xaxis: {
              title: { text: "Spot Shift (%)", font: { color: "#94a3b8" } },
              gridcolor: "#334155",
              color: "#94a3b8",
              ticksuffix: "%",
            },
            yaxis: {
              title: { text: "Vol Shift (%)", font: { color: "#94a3b8" } },
              gridcolor: "#334155",
              color: "#94a3b8",
              ticksuffix: "%",
            },
            font: { color: "#94a3b8" },
          }}
          config={{ responsive: true }}
          className="w-full"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-sm font-medium text-emerald-400">Best case</div>
          <div className="text-2xl font-mono text-emerald-300 mt-1">
            +₹
            {Math.round(
              best?.pnl ?? Math.max(...flat),
            ).toLocaleString("en-IN")}
          </div>
          {/* Read off the actual argmax cell — the direction depends on the
              book and cannot be asserted from a fixed caption. */}
          <div className="text-xs text-slate-500 mt-1">
            {best ? describe(best) : "across the grid"}
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-sm font-medium text-red-400">Worst case</div>
          <div className="text-2xl font-mono text-red-300 mt-1">
            -₹
            {Math.abs(
              Math.round(worst?.pnl ?? Math.min(...flat)),
            ).toLocaleString("en-IN")}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {worst ? describe(worst) : "across the grid"}
          </div>
        </div>
      </div>
    </div>
  );
}
