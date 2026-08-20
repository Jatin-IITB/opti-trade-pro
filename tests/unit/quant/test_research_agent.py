"""Tests for research agents (ADR-022).

Verifies:
1. GridSearchAgent produces proposals by varying each parameter.
2. All proposals have valid VRPConfig (post_init passes).
3. LLMResearchAgent parses structured JSON from the backend.
4. Invalid LLM proposals are silently dropped.
"""

from __future__ import annotations

from optitrade.agents.base import LLMResponse
from optitrade.research.agent import GridSearchAgent, LLMResearchAgent, _parse_proposals
from optitrade.strategy.vrp import VRPConfig


class MockBackend:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text=self._reply)


class TestGridSearchAgent:
    def test_produces_proposals(self):
        baseline = VRPConfig(quantity=4.0, entry_vrp_min=0.03)
        agent = GridSearchAgent(steps=(0.5, 1.5))
        proposals = agent.propose(baseline)

        assert len(proposals) > 0
        assert agent.name == "grid_search"
        for p in proposals:
            assert p.source == "grid_search"
            assert p.proposal_id.startswith("grid-")
            p.config.__post_init__()

    def test_entry_vrp_must_exceed_exit(self):
        baseline = VRPConfig(entry_vrp_min=0.01, exit_vrp_max=0.0)
        agent = GridSearchAgent(steps=(0.5,))
        proposals = agent.propose(baseline)
        for p in proposals:
            assert p.config.entry_vrp_min > p.config.exit_vrp_max

    def test_structure_variant(self):
        baseline = VRPConfig(structure="straddle")
        agent = GridSearchAgent(steps=(1.5,))
        proposals = agent.propose(baseline)
        structures = {p.config.structure for p in proposals}
        assert "strangle" in structures

    def test_max_days_variants(self):
        baseline = VRPConfig(max_days_in_trade=None)
        agent = GridSearchAgent(steps=(1.5,))
        proposals = agent.propose(baseline)
        days_proposals = [p for p in proposals if "max_days_in_trade" in p.changes]
        assert len(days_proposals) == 5


class TestLLMResearchAgent:
    def test_parses_valid_json(self):
        reply = '[{"thesis": "raise entry bar to 0.05", "changes": {"entry_vrp_min": 0.05}}]'
        backend = MockBackend(reply)
        agent = LLMResearchAgent(backend)
        proposals = agent.propose(VRPConfig())

        assert len(proposals) == 1
        assert proposals[0].config.entry_vrp_min == 0.05
        assert proposals[0].source == "llm_research"

    def test_drops_invalid_proposals(self):
        reply = (
            '[{"thesis": "impossible config", '
            '"changes": {"entry_vrp_min": -1.0}}, '
            '{"thesis": "valid one", '
            '"changes": {"quantity": 8.0}}]'
        )
        proposals = _parse_proposals(reply, VRPConfig())
        assert len(proposals) == 1
        assert proposals[0].config.quantity == 8.0

    def test_handles_non_json(self):
        proposals = _parse_proposals("I don't know what to suggest.", VRPConfig())
        assert proposals == []

    def test_handles_empty_array(self):
        proposals = _parse_proposals("[]", VRPConfig())
        assert proposals == []
