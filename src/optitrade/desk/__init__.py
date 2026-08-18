"""Daily desk cycle: deterministic paper loop, kill switch and analyst agents.

The money path (:func:`run_daily_cycle`) is fully deterministic — strategy,
debate panel, risk engine, paper fills, delta hedger — with a file-based
:class:`KillSwitch` any process or human can throw. Analysts observe and
explain from the journal with self-audited, citation-carrying reports
(ADR-008/010/015).
"""

from optitrade.desk.analysts import AnalystReport, PostMortemAnalyst, SurfaceAuditor
from optitrade.desk.cycle import CycleResult, DeskConfig, run_daily_cycle
from optitrade.desk.kill_switch import KillSwitch

__all__ = [
    "AnalystReport",
    "CycleResult",
    "DeskConfig",
    "KillSwitch",
    "PostMortemAnalyst",
    "SurfaceAuditor",
    "run_daily_cycle",
]
