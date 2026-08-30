"""Tests for RawChain → ChainIn bridge."""

from options_trading.services.chain_converter import raw_chain_to_chain_in
from optitrade.data.capture import SyntheticSource


class TestRawChainToChainIn:
    def _chain(self):
        return SyntheticSource(seed=42).fetch_chain("NIFTY")

    def test_produces_chain_in(self):
        chain_in = raw_chain_to_chain_in(self._chain())
        assert chain_in.spot > 0
        assert chain_in.rate >= 0
        assert len(chain_in.quotes) > 0

    def test_quotes_have_positive_mid(self):
        chain_in = raw_chain_to_chain_in(self._chain())
        for q in chain_in.quotes:
            assert q.mid > 0, f"quote {q.strike} {q.option_type} has non-positive mid"

    def test_quotes_have_valid_option_type(self):
        chain_in = raw_chain_to_chain_in(self._chain())
        for q in chain_in.quotes:
            assert q.option_type in ("call", "put")

    def test_quotes_have_positive_expiry(self):
        chain_in = raw_chain_to_chain_in(self._chain())
        for q in chain_in.quotes:
            assert q.expiry > 0

    def test_spot_matches_source(self):
        chain = self._chain()
        chain_in = raw_chain_to_chain_in(chain)
        assert chain_in.spot == chain.spot

    def test_fewer_quotes_after_filtering(self):
        chain = self._chain()
        chain_in = raw_chain_to_chain_in(chain)
        assert len(chain_in.quotes) <= len(chain.quotes)

    def test_deterministic(self):
        a = raw_chain_to_chain_in(self._chain())
        b = raw_chain_to_chain_in(self._chain())
        assert len(a.quotes) == len(b.quotes)
        for qa, qb in zip(a.quotes, b.quotes, strict=True):
            assert qa.strike == qb.strike
            assert qa.mid == qb.mid
