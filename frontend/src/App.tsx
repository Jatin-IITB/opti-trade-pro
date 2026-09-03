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
  ChevronDown,
  ChevronRight,
  FlaskConical,
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
import { HoldingsTable } from "./components/HoldingsTable";
import { PortfolioSummaryPanel } from "./components/PortfolioSummary";
import { PositionSignals } from "./components/PositionSignals";
import { ConnectorsPanel } from "./components/ConnectorsPanel";
import { DeskPanel } from "./components/DeskPanel";
import { HistoryGate } from "./components/HistoryGate";
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

/**
 * Where these panels' numbers come from.
 *
 * Two kinds: panels describing your *broker account* (Risk, Scenarios, P&L
 * Explain, Positions) and panels describing the *market*, computed by the
 * quant engines from a captured option chain (Vol Surface, eSSVI, Chain,
 * Greeks, Higher-Order, VRP, Backtest). Market panels fall back to bundled
 * demo data until the first capture cycle completes; account panels report
 * that they have no book rather than inventing one.
 *
 * There is no third, "synthetic" kind any more — every panel has a real data
 * path. The three needing days of history (VRP, Backtest, P&L Explain) report
 * what they are still missing through `HistoryGate` instead of rendering a
 * stand-in, and their fabricated demo series were deleted outright so there
 * is nothing left to fall back to.
 *
 * This was a `DataSource` union on each nav item, but its only consumer was
 * the "Sim" badge, which is gone. Kept as prose rather than an unread field.
 */
interface NavItem {
  id: string;
  label: string;
  icon: typeof Layers;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Volatility",
    items: [
      { id: "surface", label: "Vol Surface", icon: Layers },
      { id: "essvi", label: "eSSVI Fit", icon: Target },
      { id: "chain", label: "Option Chain", icon: Table },
    ],
  },
  {
    label: "Greeks & Risk",
    items: [
      {
        id: "greeks",
        label: "Greeks Book",
        icon: BarChart3,
      },
      { id: "higher", label: "Higher-Order", icon: Zap },
      { id: "risk", label: "Risk", icon: Shield },
    ],
  },
  {
    label: "P&L & Scenarios",
    items: [
      { id: "pnl", label: "P&L Explain", icon: TrendingUp },
      {
        id: "scenarios",
        label: "Scenarios",
        icon: Grid3x3,
      },
    ],
  },
  {
    label: "Strategy",
    items: [
      {
        id: "backtest",
        label: "Backtest",
        icon: LineChart,
      },
      { id: "vrp", label: "VRP Signal", icon: Activity },
      { id: "desk", label: "Paper Desk", icon: FlaskConical },
    ],
  },
  {
    label: "Portfolio",
    items: [
      { id: "portfolio", label: "Positions", icon: Briefcase },
      { id: "connectors", label: "Connectors", icon: Plug },
    ],
  },
];

type TabId = string;

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

function SidebarGroup({
  group,
  activeTab,
  onSelect,
  defaultOpen,
}: {
  group: NavGroup;
  activeTab: TabId;
  onSelect: (id: TabId) => void;
  defaultOpen: boolean;
}) {
  const hasActive = group.items.some((i) => i.id === activeTab);
  const [open, setOpen] = useState(defaultOpen || hasActive);

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-400 transition-colors"
      >
        {group.label}
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && (
        <div className="space-y-0.5 pb-2">
          {group.items.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => onSelect(id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors ${
                activeTab === id
                  ? "bg-blue-600/15 text-blue-400 font-medium"
                  : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              <Icon size={15} className="shrink-0" />
              <span className="flex-1 text-left">{label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("surface");
  const live = useLiveData();
  const portfolio = usePortfolio();

  const { data } = live;
  const badge = STATUS_STYLE[live.status] ?? STATUS_STYLE.disconnected;

  return (
    <div className="h-screen flex flex-col bg-slate-900">
      <header className="border-b border-slate-700 bg-slate-900/80 backdrop-blur-sm shrink-0 z-50">
        <div className="px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-400 flex items-center justify-center text-white font-bold text-sm"
              role="img"
              aria-label="OptiTrade Pro logo"
            >
              OT
            </div>
            <div>
              <h1 className="text-base font-semibold text-slate-100">
                OptiTrade Pro
              </h1>
              <p className="text-[11px] text-slate-500">
                Volatility Analytics Desk
              </p>
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

      <div className="flex flex-1 overflow-hidden">
        <aside className="w-52 shrink-0 border-r border-slate-700/50 bg-slate-900 overflow-y-auto py-3 px-2 space-y-1">
          {NAV_GROUPS.map((group, i) => (
            <SidebarGroup
              key={group.label}
              group={group}
              activeTab={activeTab}
              onSelect={setActiveTab}
              defaultOpen={i === 0}
            />
          ))}
          <div className="pt-3 px-3 border-t border-slate-800 mt-3">
            <div className="text-[10px] text-slate-600 leading-relaxed">
              4 Greeks methods &middot; 26 ADRs
              <br />
              fail-closed risk engine
            </div>
          </div>
        </aside>

        <main className="flex-1 overflow-y-auto">
          <div className="max-w-[1200px] mx-auto px-6 py-6">
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
              {activeTab === "pnl" && (
                <HistoryGate data={data.pnlExplain} title="P&L Attribution">
                  <PnlWaterfall data={data.pnlExplain} />
                </HistoryGate>
              )}
              {activeTab === "higher" && (
                <HigherOrderGreeks data={data.higherOrderGreeks} />
              )}
              {activeTab === "chain" && (
                <OptionChain data={data.optionChain} />
              )}
              {activeTab === "backtest" && (
                <HistoryGate
                  data={data.backtestEquity}
                  title="Walk-Forward Backtest"
                >
                  <BacktestEquity data={data.backtestEquity} />
                </HistoryGate>
              )}
              {activeTab === "vrp" && (
                <HistoryGate data={data.vrpSignal} title="Variance Risk Premium">
                  <VrpSignal data={data.vrpSignal} />
                </HistoryGate>
              )}
              {activeTab === "risk" && (
                <RiskDashboard data={data.riskDashboard} />
              )}
              {/* No HistoryGate around the whole panel: the kill switch must
                  stay reachable on a desk that has never run, so the gate
                  sits inside DeskPanel around the cycle history alone. */}
              {activeTab === "desk" && <DeskPanel data={data.desk} />}
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
                    <>
                      <PositionSignals signals={portfolio.signals} />
                      <HoldingsTable holdings={portfolio.holdings} />
                    </>
                  )}
                </div>
              )}
              {activeTab === "connectors" && <ConnectorsPanel />}
            </ErrorBoundary>
          </div>
        </main>
      </div>
    </div>
  );
}
