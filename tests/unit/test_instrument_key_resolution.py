"""Instrument lookup must not resolve a near-miss symbol.

Found while driving the paper desk from real captured data: the capture
schedule could not start, reporting "No live contracts for
``NSE_INDEX|Nifty 500``" for a configured underlying of Nifty 50. Nifty 50 is
stored with ``tradingsymbol='NIFTY'`` and ``name='Nifty 50'``, so searching
for "Nifty 50" missed the exact match on ``tradingsymbol``, and the relaxed
substring pass then matched "NIFTY 500" on that same first column and
returned it — never reaching ``name``, where the exact value sat.

The mis-resolution was silent: the caller received a well-formed instrument
key for the wrong index. These tests pin exact-before-partial ordering
against a fixture shaped like the real Upstox dataset, so they carry no
network dependence.
"""

from __future__ import annotations

import pandas as pd
import pytest

from options_trading.api.tools.instrument_key import _match_instrument

pytestmark = pytest.mark.unit


@pytest.fixture()
def instruments() -> pd.DataFrame:
    """Rows shaped like the real NSE_INDEX slice of the Upstox dump.

    ``tradingsymbol`` is uppercased at load, ``name`` is not — mirrored here
    so the casing behaviour under test is the deployed one.
    """
    return pd.DataFrame(
        [
            # Listed before Nifty 50 on purpose: the old code took iloc[0] of
            # the substring matches, so ordering decided the answer.
            {
                "instrument_key": "NSE_INDEX|Nifty 500",
                "tradingsymbol": "NIFTY 500",
                "name": "Nifty 500",
            },
            {
                "instrument_key": "NSE_INDEX|Nifty 50",
                "tradingsymbol": "NIFTY",
                "name": "Nifty 50",
            },
            {
                "instrument_key": "NSE_INDEX|Nifty Bank",
                "tradingsymbol": "BANKNIFTY",
                "name": "Nifty Bank",
            },
            {
                "instrument_key": "NSE_INDEX|Nifty Midcap 50",
                "tradingsymbol": "NIFTY MID 50",
                "name": "Nifty Midcap 50",
            },
        ]
    )


class TestExactBeatsPartial:
    def test_nifty_50_resolves_to_nifty_50_not_nifty_500(self, instruments):
        """The regression that stopped every capture on this machine."""
        row, is_exact = _match_instrument(instruments, "NIFTY 50")

        assert row["instrument_key"] == "NSE_INDEX|Nifty 50"
        assert is_exact is True

    def test_an_exact_name_match_outranks_a_substring_tradingsymbol_match(self, instruments):
        """`name` holds the exact value; it must be consulted before guessing."""
        row, is_exact = _match_instrument(instruments, "NIFTY MIDCAP 50")

        assert row["instrument_key"] == "NSE_INDEX|Nifty Midcap 50"
        assert is_exact is True

    def test_the_short_code_still_resolves(self, instruments):
        row, is_exact = _match_instrument(instruments, "NIFTY")

        assert row["instrument_key"] == "NSE_INDEX|Nifty 50"
        assert is_exact is True

    def test_nifty_500_still_resolves_to_itself(self, instruments):
        """The fix must not overcorrect into the opposite error."""
        row, is_exact = _match_instrument(instruments, "NIFTY 500")

        assert row["instrument_key"] == "NSE_INDEX|Nifty 500"
        assert is_exact is True

    def test_bank_nifty_resolves_by_either_spelling(self, instruments):
        for query in ("BANKNIFTY", "NIFTY BANK"):
            row, is_exact = _match_instrument(instruments, query)
            assert row["instrument_key"] == "NSE_INDEX|Nifty Bank"
            assert is_exact is True


class TestPartialFallback:
    def test_a_partial_match_is_flagged_as_inexact(self, instruments):
        """The caller logs a warning on these; silence is what caused the bug."""
        match = _match_instrument(instruments, "MIDCAP")

        assert match is not None
        _, is_exact = match
        assert is_exact is False

    def test_the_shortest_candidate_wins_a_partial_match(self, instruments):
        """A prefix of several symbols resolves to the closest, not the first."""
        frame = pd.DataFrame(
            [
                {"instrument_key": "K|LONGER", "tradingsymbol": "ABC PLUS", "name": "Abc Plus"},
                {"instrument_key": "K|SHORT", "tradingsymbol": "ABC", "name": "Abc"},
            ]
        )

        row, is_exact = _match_instrument(frame, "ABC")

        assert row["instrument_key"] == "K|SHORT"
        assert is_exact is True

    def test_no_match_returns_none(self, instruments):
        assert _match_instrument(instruments, "SENSEX") is None

    def test_a_regex_metacharacter_is_matched_literally(self, instruments):
        """A symbol is not a pattern; `.` must not match any character."""
        assert _match_instrument(instruments, "NIFTY 5.0") is None
