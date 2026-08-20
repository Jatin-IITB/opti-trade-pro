"""Research loop: propose parameter changes, evaluate via walk-forward (ADR-022).

Agents propose → backtest-as-tool evaluates → humans approve → accepted
changes land as ADRs. The loop is deterministic at its core: the LLM
proposes, but evaluation is pure math.
"""

from optitrade.research.agent import GridSearchAgent, LLMResearchAgent, ResearchAgent
from optitrade.research.evaluator import ProposalEvaluator
from optitrade.research.loop import ResearchLoop
from optitrade.research.proposals import ExperimentResult, ResearchProposal, ResearchReport

__all__ = [
    "ExperimentResult",
    "GridSearchAgent",
    "LLMResearchAgent",
    "ProposalEvaluator",
    "ResearchAgent",
    "ResearchLoop",
    "ResearchProposal",
    "ResearchReport",
]
