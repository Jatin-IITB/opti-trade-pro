import { Component, useState } from "react";
import type { ErrorInfo, ReactNode } from "react";
import {
  BarChart3,
  Grid3x3,
  TrendingUp,
  Zap,
  Table,
  Layers,
  LineChart,
  Shield,
  Activity,
  Target,
  Briefcase,
  Plug,
} from "lucide-react";
import { VolSurface } from "./components/VolSurface";
import { ScenarioHeatmap } from "./components/ScenarioHeatmap";
import { PnlWaterfall } from "./components/PnlWaterfall";
import { HigherOrderGreeks } from "./components/HigherOrderGreeks";
import { OptionChain } from "./components/OptionChain";
import { GreeksBook } from "./components/GreeksBook";
import { EssviCalibration } from "./components/EssviCalibration";
import { BacktestEquity } from "./components/BacktestEquity";
import { VrpSignal } from "./components/VrpSignal";
import { RiskDashboard } from "./components/RiskDashboard";
import { PortfolioSummaryPanel } from "./components/PortfolioSummary";
import { PositionSignals } from "./components/PositionSignals";
import { ConnectorsPanel } from "./components/ConnectorsPanel";
import { usePortfolio } from "./hooks/usePortfolio";
import { useLiveData } from "./hooks/useLiveData";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Dashboard error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-8 text-center">
          <div className="text-red-400 text-lg font-medium mb-2">
            Panel failed to render
          </div>
          <div className="text-slate-500 text-sm font-mono">
            {this.state.error.message}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-4 px-4 py-2 rounded bg-slate-700 text-slate-300 text-sm hover:bg-slate-600"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const TABS = [
  { id: "surface", label: "Vol Surface", icon: Layers },
  { id: "essvi", label: "eSSVI Fit", icon: Target },
  { id: "greeks", label: "Greeks Book", icon: BarChart3 },
  { id: "scenarios", label: "Scenarios", icon: Grid3x3 },
  { id: "pnl", label: "P&L Explain", icon: TrendingUp },
  { id: "higher", label: "Higher-Order", icon: Zap },
  { id: "chain", label: "Option Chain", icon: Table },
  { id: "backtest", label: "Backtest", icon: LineChart },
  { id: "vrp", label: "VRP Signal", icon: Activity },
  { id: "risk", label: "Risk", icon: Shield },
  { id: "portfolio", label: "Portfolio", icon: Briefcase },
  { id: "connectors", label: "Connectors", icon: Plug },
] as const;

type TabId = (typeof TABS)[number]["id"];

const STATUS_STYLE: Record<string, { dot: string; label: string; bg: string }> =
  {
    connected: {
      dot: "bg-emerald-400",
      label: "Live",
      bg: "bg-emerald-900/30 text-emerald-400",
    },
    connecting: {
      dot: "bg-amber-400 animate-pulse",
      label: "Connecting…",
      bg: "bg-amber-900/30 text-amber-400",
    },
    disconnected: {
      dot: "bg-slate-500",
      label: "Demo Mode",
      bg: "bg-slate-800 text-teal-400",
    },
    error: {
      dot: "bg-red-400",
      label: "Demo Mode",
      bg: "bg-slate-800 text-teal-400",
    },
  };

function timeSince(ts: number | null): string {
  if (!ts) return "";
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  return `${Math.floor(sec / 60)}m ago`;
}

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("surface");
  const live = useLiveData();
  const portfolio = usePortfolio();

  const { data } = live;
  const badge = STATUS_STYLE[live.status] ?? STATUS_STYLE.disconnected;

  return (
    <div className="min-h-screen bg-slate-900">
      <header className="border-b border-slate-700 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-[1400px] mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-400 flex items-center justify-center text-white font-bold text-sm"
              role="img"
              aria-label="OptiTrade Pro logo"
            >
              OT
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-100">
                OptiTrade Pro
              </h1>
              <p className="text-xs text-slate-400">Analytics Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <span>
              {live.underlying}{" "}
              <span className="text-emerald-400 font-mono">
                {live.spot.toLocaleString()}
              </span>
            </span>
            {live.lastUpdate && (
              <span className="text-slate-500">
                {timeSince(live.lastUpdate)}
              </span>
            )}
            <span
              className={`px-2 py-1 rounded font-mono flex items-center gap-1.5 ${badge.bg}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${badge.dot}`} />
              {badge.label}
            </span>
          </div>
        </div>
      </header>

      <nav
        className="border-b border-slate-700 bg-slate-900/60"
        aria-label="Dashboard panels"
      >
        <div className="max-w-[1400px] mx-auto px-6 flex gap-1 overflow-x-auto">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              aria-selected={activeTab === id}
              aria-label={label}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === id
                  ? "border-blue-500 text-blue-400"
                  : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
              }`}
            >
              <Icon size={16} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      </nav>

      <main className="max-w-[1400px] mx-auto px-6 py-6">
        <ErrorBoundary>
          {activeTab === "surface" && (
            <VolSurface data={data.volSurface} />
          )}
          {activeTab === "essvi" && (
            <EssviCalibration data={data.essviCalibration} />
          )}
          {activeTab === "greeks" && (
            <GreeksBook data={data.greeksComparison} />
          )}
          {activeTab === "scenarios" && (
            <ScenarioHeatmap data={data.scenarioGrid} />
          )}
          {activeTab === "pnl" && <PnlWaterfall data={data.pnlExplain} />}
          {activeTab === "higher" && (
            <HigherOrderGreeks data={data.higherOrderGreeks} />
          )}
          {activeTab === "chain" && (
            <OptionChain data={data.optionChain} />
          )}
          {activeTab === "backtest" && (
            <BacktestEquity data={data.backtestEquity} />
          )}
          {activeTab === "vrp" && <VrpSignal data={data.vrpSignal} />}
          {activeTab === "risk" && (
            <RiskDashboard data={data.riskDashboard} />
          )}
          {activeTab === "portfolio" && (
            <div className="space-y-6">
              <PortfolioSummaryPanel
                summary={portfolio.summary}
                syncStatus={portfolio.syncStatus}
                loading={portfolio.loading}
                error={portfolio.error}
                onRefresh={portfolio.refresh}
              />
              {portfolio.summary?.synced && (
                <PositionSignals signals={portfolio.signals} />
              )}
            </div>
          )}
          {activeTab === "connectors" && <ConnectorsPanel />}
        </ErrorBoundary>
      </main>

      <footer className="border-t border-slate-800 py-4 mt-auto">
        <div className="max-w-[1400px] mx-auto px-6 flex justify-between text-xs text-slate-500">
          <span>
            4 Greeks methods cross-validated &middot; 23 ADRs &middot;
            fail-closed risk engine
          </span>
          <span>OptiTrade Pro v3.0</span>
        </div>
      </footer>
    </div>
  );
}
