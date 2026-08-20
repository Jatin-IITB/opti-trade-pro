"""Exact Shapley-value attribution.

Used to attribute portfolio P&L across strategies or desks fairly: the
characteristic function ``value`` maps a coalition of players (strategies)
to the P&L that coalition would have produced, and each player's Shapley
value is its average marginal contribution over all join orders. The
efficiency axiom guarantees the full P&L is distributed: the values sum to
``value(all players)`` minus ``value(empty set)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import combinations
from math import factorial

_MAX_PLAYERS = 12  # exact enumeration is O(2^n); 2^12 = 4096 coalitions


def shapley_values(
    players: Sequence[str],
    value: Callable[[frozenset[str]], float],
) -> dict[str, float]:
    """Exact Shapley values by enumeration over all coalitions.

    ``value`` is called once per subset of ``players`` (including the empty
    set), so it must be defined on every coalition. Guarded to
    ``len(players) <= 12``; beyond that use a sampling approximation.
    """
    n = len(players)
    if n > _MAX_PLAYERS:
        raise ValueError(
            f"exact Shapley enumeration is limited to {_MAX_PLAYERS} players, got {n}; "
            "use Monte Carlo permutation sampling for larger games"
        )
    if len(set(players)) != n:
        raise ValueError("players must be unique")
    if n == 0:
        return {}

    # Cache every coalition value: each is needed many times below.
    coalition_value: dict[frozenset[str], float] = {}
    for size in range(n + 1):
        for combo in combinations(players, size):
            coalition = frozenset(combo)
            coalition_value[coalition] = value(coalition)

    # phi_i = sum over S not containing i of |S|! (n-|S|-1)! / n! * (v(S+i) - v(S))
    n_factorial = factorial(n)
    values: dict[str, float] = {}
    for player in players:
        others = [p for p in players if p != player]
        phi = 0.0
        for size in range(n):
            weight = factorial(size) * factorial(n - size - 1) / n_factorial
            for combo in combinations(others, size):
                without = frozenset(combo)
                phi += weight * (coalition_value[without | {player}] - coalition_value[without])
        values[player] = phi
    return values


__all__ = ["shapley_values"]
