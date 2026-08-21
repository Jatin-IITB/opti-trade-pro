import { useState } from "react";
import {
  BarChart3,
  Grid3x3,
  TrendingUp,
  Zap,
  Table,
  Layers,
} from "lucide-react";
import demoData from "./data/demo.json";
import { VolSurface } from "./components/VolSurface";
import { ScenarioHeatmap } from "./components/ScenarioHeatmap";
import { PnlWaterfall } from "./components/PnlWaterfall";
import { HigherOrderGreeks } from "./components/HigherOrderGreeks";
import { OptionChain } from "./components/OptionChain";
import { GreeksBook } from "./components/GreeksBook";

const TABS = [
  { id: "surface", label: "Vol Surface", icon: Layers },
  { id: "greeks", label: "Greeks Book", icon: BarChart3 },
  { id: "scenarios", label: "Scenarios", icon: Grid3x3 },
  { id: "pnl", label: "P&L Explain", icon: TrendingUp },
  { id: "higher", label: "Higher-Order", icon: Zap },
  { id: "chain", label: "Option Chain", icon: Table },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("surface");

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Header */}
      <header className="border-b border-slate-700 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-[1400px] mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-400 flex items-center justify-center text-white font-bold text-sm">
              OT
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-100">
                OptiTrade Pro
              </h1>
              <p className="text-xs text-slate-400">
                Analytics Dashboard
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <span>
              NIFTY{" "}
              <span className="text-emerald-400 font-mono">
                {demoData.volSurface.spot.toLocaleString()}
              </span>
            </span>
            <span className="px-2 py-1 rounded bg-slate-800 text-teal-400 font-mono">
              Demo Mode
            </span>
          </div>
        </div>
      </header>

      {/* Tab Navigation */}
      <nav className="border-b border-slate-700 bg-slate-900/60">
        <div className="max-w-[1400px] mx-auto px-6 flex gap-1 overflow-x-auto">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === id
                  ? "border-blue-500 text-blue-400"
                  : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
              }`}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-[1400px] mx-auto px-6 py-6">
        {activeTab === "surface" && (
          <VolSurface data={demoData.volSurface} />
        )}
        {activeTab === "greeks" && (
          <GreeksBook data={demoData.greeksComparison} />
        )}
        {activeTab === "scenarios" && (
          <ScenarioHeatmap data={demoData.scenarioGrid} />
        )}
        {activeTab === "pnl" && (
          <PnlWaterfall data={demoData.pnlExplain} />
        )}
        {activeTab === "higher" && (
          <HigherOrderGreeks data={demoData.higherOrderGreeks} />
        )}
        {activeTab === "chain" && (
          <OptionChain data={demoData.optionChain} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 py-4 mt-auto">
        <div className="max-w-[1400px] mx-auto px-6 flex justify-between text-xs text-slate-500">
          <span>
            4 Greeks methods cross-validated &middot; 571 deterministic tests &middot; 23 ADRs
          </span>
          <span>OptiTrade Pro v3.0</span>
        </div>
      </footer>
    </div>
  );
}
