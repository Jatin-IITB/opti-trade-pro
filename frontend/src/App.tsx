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
 * Where a panel's numbers come from.
 *
 * - `account`  — your broker account. Real money, real positions.
 * - `market`   — computed by the quant engines from a captured live option
 *                chain. Falls back to bundled demo data until the first
 *                capture cycle completes.
 * - `synthetic`— genuine engine output over *simulated* inputs. There is no
 *                live data path for these yet, so nothing here describes your
 *                book or the current market.
 */
type DataSource = "account" | "market" | "synthetic";

interface NavItem {
  id: string;
  label: string;
  icon: typeof Layers;
  source: DataSource;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Volatility",
    items: [
      { id: "surface", label: "Vol Surface", icon: Layers, source: "market" },
      { id: "essvi", label: "eSSVI Fit", icon: Target, source: "market" },
      { id: "chain", label: "Option Chain", icon: Table, source: "market" },
    ],
  },
  {
    label: "Greeks & Risk",
    items: [
      {
        id: "greeks",
        label: "Greeks Book",
        icon: BarChart3,
        source: "market",
      },
      { id: "higher", label: "Higher-Order", icon: Zap, source: "market" },
      { id: "risk", label: "Risk", icon: Shield, source: "account" },
    ],
  },
  {
    label: "P&L & Scenarios",
    items: [
      { id: "pnl", label: "P&L Explain", icon: TrendingUp, source: "synthetic" },
      {
        id: "scenarios",
        label: "Scenarios",
        icon: Grid3x3,
        source: "account",
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
        source: "synthetic",
      },
      { id: "vrp", label: "VRP Signal", icon: Activity, source: "synthetic" },
    ],
  },
  {
    label: "Portfolio",
    items: [
      { id: "portfolio", label: "Positions", icon: Briefcase, source: "account" },
      { id: "connectors", label: "Connectors", icon: Plug, source: "account" },
    ],
  },
];

/** Why a given panel cannot yet show real numbers. */
const SYNTHETIC_REASON: Record<string, string> = {
  pnl: "P&L attribution needs two consecutive daily portfolio snapshots plus the vol surface between them. Snapshot history is not yet accumulating.",
  backtest:
    "This equity curve is a seeded random walk, not a strategy result. The real walk-forward engine with deflated Sharpe exists but is not connected to this panel.",
  vrp: "The IV and realised-vol series are simulated. Real VRP needs a persisted ATM-IV time series and realised vol from spot history.",
};

function SyntheticNotice({ tabId }: { tabId: string }) {
  const reason = SYNTHETIC_REASON[tabId];
  if (!reason) return null;
  return (
    <div className="mb-4 rounded-lg border border-amber-700/40 bg-amber-950/30 px-4 py-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
          Synthetic
        </span>
        <p className="text-xs leading-relaxed text-amber-200/80">
          Engine output on <strong>simulated inputs</strong> — this does not
          reflect your account or current market. {reason}
        </p>
      </div>
    </div>
  );
}

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
          {group.items.map(({ id, label, icon: Icon, source }) => (
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
              {source === "synthetic" && (
                <span
                  title="Synthetic — engine output on simulated inputs, not your account"
                  className="shrink-0 rounded bg-amber-500/15 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-400/90"
                >
                  Sim
                </span>
              )}
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
            <SyntheticNotice tabId={activeTab} />
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
