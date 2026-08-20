"""Tests for exact Shapley-value attribution."""

from __future__ import annotations

import pytest

from optitrade.attribution import shapley_values


def _game(values: dict[frozenset[str], float]):
    return lambda coalition: values[coalition]


class TestAxioms:
    def test_efficiency_full_value_is_distributed(self):
        # Superadditive game: v(S) = len(S)^2 (strategies reinforce each other).
        players = ["momentum", "carry", "vol_arb", "hedge"]
        phi = shapley_values(players, lambda s: float(len(s) ** 2))
        assert sum(phi.values()) == pytest.approx(16.0)  # v(all) - v(empty)

    def test_symmetry_identical_players_get_identical_credit(self):
        players = ["momentum", "carry", "vol_arb", "hedge"]
        phi = shapley_values(players, lambda s: float(len(s) ** 2))
        first = next(iter(phi.values()))
        assert all(v == pytest.approx(first) for v in phi.values())

    def test_dummy_player_gets_zero(self):
        # Only "alpha" produces P&L; "passive" adds nothing to any coalition.
        def value(coalition: frozenset[str]) -> float:
            return 5.0 if "alpha" in coalition else 0.0

        phi = shapley_values(["alpha", "passive"], value)
        assert phi["alpha"] == pytest.approx(5.0)
        assert phi["passive"] == pytest.approx(0.0)


class TestHandComputedGame:
    def test_three_player_game_matches_manual_computation(self):
        values = {
            frozenset(): 0.0,
            frozenset({"a"}): 10.0,
            frozenset({"b"}): 0.0,
            frozenset({"c"}): 0.0,
            frozenset({"a", "b"}): 20.0,
            frozenset({"a", "c"}): 20.0,
            frozenset({"b", "c"}): 5.0,
            frozenset({"a", "b", "c"}): 30.0,
        }
        phi = shapley_values(["a", "b", "c"], _game(values))
        # phi_a = 1/3*10 + 1/6*20 + 1/6*20 + 1/3*25 = 55/3
        assert phi["a"] == pytest.approx(55.0 / 3.0)
        # phi_b = 1/3*0 + 1/6*10 + 1/6*5 + 1/3*10 = 35/6, symmetric with c
        assert phi["b"] == pytest.approx(35.0 / 6.0)
        assert phi["c"] == pytest.approx(35.0 / 6.0)
        assert sum(phi.values()) == pytest.approx(30.0)


class TestGuards:
    def test_more_than_twelve_players_raises_value_error(self):
        players = [f"desk_{i}" for i in range(13)]
        with pytest.raises(ValueError, match="12"):
            shapley_values(players, lambda s: float(len(s)))

    def test_twelve_players_is_still_allowed(self):
        players = [f"desk_{i}" for i in range(12)]
        phi = shapley_values(players, lambda s: float(len(s)))
        assert sum(phi.values()) == pytest.approx(12.0)

    def test_duplicate_players_raise(self):
        with pytest.raises(ValueError, match="unique"):
            shapley_values(["a", "a"], lambda s: 0.0)

    def test_no_players_returns_empty(self):
        assert shapley_values([], lambda s: 0.0) == {}
