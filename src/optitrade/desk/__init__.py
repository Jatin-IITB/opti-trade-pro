"""Daily desk cycle: deterministic paper loop, kill switch and analyst agents.

The money path (:func:`run_daily_cycle`) is fully deterministic — strategy,
debate panel, risk engine, paper fills, delta hedger — with a file-based
:class:`KillSwitch` any process or human can throw. Analysts observe and
explain from the journal with self-audited, citation-carrying reports, and
:func:`build_daily_report` assembles them into the daily markdown artifact
(ADR-008/010/015/018).
"""

from optitrade.desk.analysts import (
    AnalystReport,
    PostMortemAnalyst,
    RegimeAnalyst,
    RiskOfficerAnalyst,
    ScenarioQuery,
    SurfaceAuditor,
)
from optitrade.desk.cycle import CycleResult, DeskConfig, run_daily_cycle
from optitrade.desk.kill_switch import KillSwitch
from optitrade.desk.report import DailyReport, build_daily_report

__all__ = [
    "AnalystReport",
    "CycleResult",
    "DailyReport",
    "DeskConfig",
    "KillSwitch",
    "PostMortemAnalyst",
    "RegimeAnalyst",
    "RiskOfficerAnalyst",
    "ScenarioQuery",
    "SurfaceAuditor",
    "build_daily_report",
    "run_daily_cycle",
]
