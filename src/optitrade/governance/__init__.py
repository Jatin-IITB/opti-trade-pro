"""Expert debate panel for trade-decision review (ADR-010).

Rule-based experts are the default and fully deterministic. The LLM-backed
:class:`LLMExpert` needs the optional ``agentic`` extra (dspy); this package
imports cleanly without it.
"""

from optitrade.governance.debate import DebatePanel, DecisionRecord
from optitrade.governance.dspy_adapter import LLMExpert
from optitrade.governance.experts import (
    ExecutionExpert,
    Expert,
    ExpertOpinion,
    RiskOfficer,
    Stance,
    StrategyExpert,
    TradeProposal,
)

__all__ = [
    "DebatePanel",
    "DecisionRecord",
    "ExecutionExpert",
    "Expert",
    "ExpertOpinion",
    "LLMExpert",
    "RiskOfficer",
    "Stance",
    "StrategyExpert",
    "TradeProposal",
]
