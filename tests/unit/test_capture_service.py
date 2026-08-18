# tests/unit/test_capture_service.py
"""Unit tests for the Upstox chain-capture adapter (no network, deterministic clock).

``fetch_live_option_chain`` is monkeypatched where ``capture_service`` imports it,
so no test touches the Upstox API. The fixture replicates the real v2 payload
shape, including field-name aliases and two planted bad quotes (one crossed book,
one zero-bid wing) so the hygiene filters have real work to do.
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from options_trading.api import dependencies
from options_trading.api.routes import capture as capture_routes
from options_trading.services import capture_service
from options_trading.services.capture_service import (
    MIN_EXPIRY_YEARS,
    CaptureReport,
    UpstoxCaptureSource,
    capture_and_store,
)
from options_trading.utils.exceptions import DataQualityError
from optitrade.core.types import OptionType
from optitrade.data import RawChain, RawQuote, SnapshotStore

SPOT = 24_512.35
EXPIRY_DATE = "2026-08-27"
# Expiry-day NSE close (15:30 IST) as a unix epoch — the anchor for year fractions.
EXPIRY_EPOCH = datetime(2026, 8, 27, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
NOW_EPOCH = EXPIRY_EPOCH - 5 * 86_400.0  # exactly 5 days before expiry close


def _leg(
    bid: float, ask: float, ltp: float, volume: int, oi: int, bid_qty: int, ask_qty: int
) -> dict:
    """One Upstox leg in the standard v2 key spelling."""
    return {
        "instrument_key": "NSE_FO|00000",
        "market_data": {
            "ltp": ltp,
            "bid_price": bid,
            "bid_qty": bid_qty,
            "ask_price": ask,
            "ask_qty": ask_qty,
            "volume": volume,
            "oi": oi,
        },
        "option_greeks": {"iv": 13.5, "delta": 0.5},
    }


# Real payload shape: {"status": "success", "data": [rows]}. Rows are deliberately
# out of strike order (adapter must sort), use several key-alias spellings, and
# plant one crossed book (24400 CE) and one zero-bid wing (24700 CE).
UPSTOX_CHAIN_FIXTURE: dict = {
    "status": "success",
    "data": [
        # Junk row with no strike under any alias: must be skipped entirely.
        {"expiry": EXPIRY_DATE, "pcr": 1.02, "call_options": _leg(1.0, 1.2, 1.1, 10, 10, 5, 5)},
        {
            "expiry": EXPIRY_DATE,
            "strike_price": 24_500.0,
            "underlying_key": "NSE_INDEX|Nifty 50",
            "underlying_spot_price": SPOT,
            "call_options": _leg(180.55, 183.40, 182.00, 250_000, 1_800_000, 1500, 900),
            "put_options": _leg(168.20, 170.90, 169.50, 230_000, 1_650_000, 1200, 1100),
        },
        {
            "expiry": EXPIRY_DATE,
            "strike_price": 24_200.0,
            "underlying_spot_price": SPOT,
            "call_options": _leg(331.55, 336.00, 334.10, 120_000, 900_000, 750, 600),
            "put_options": _leg(41.50, 42.85, 42.10, 340_000, 2_100_000, 2000, 1800),
        },
        {
            # Alias spellings: strike / call_option / put_option, market_data keys
            # bid, best_ask_price, close_price, vol, open_interest; no depth qtys.
            "expiry": EXPIRY_DATE,
            "strike": 24_300.0,
            "underlying_spot_price": SPOT,
            "call_option": {
                "market_data": {
                    "bid": 262.00,
                    "best_ask_price": 265.40,
                    "close_price": 263.70,
                    "vol": 98_000,
                    "open_interest": 750_000,
                }
            },
            "put_option": {
                "market_data": {
                    "bid": 71.35,
                    "best_ask_price": 72.90,
                    "close_price": 72.00,
                    "vol": 280_000,
                    "open_interest": 1_900_000,
                }
            },
        },
        {
            # Planted crossed book on the call: bid 215 > ask 210.
            "expiry": EXPIRY_DATE,
            "strike_price": 24_400.0,
            "underlying_spot_price": SPOT,
            "call_options": _leg(215.00, 210.00, 212.00, 50_000, 400_000, 300, 250),
            "put_options": _leg(96.40, 98.10, 97.20, 310_000, 2_000_000, 1700, 1600),
        },
        {
            # Alias spellings CE / PE for the legs.
            "expiry": EXPIRY_DATE,
            "strikePrice": 24_600.0,
            "underlying_spot_price": SPOT,
            "CE": _leg(121.05, 123.20, 122.00, 275_000, 1_700_000, 1400, 1300),
            "PE": _leg(209.00, 212.40, 210.60, 90_000, 800_000, 500, 450),
        },
        {
            # Planted zero-bid wing on the call: one-sided book, mid unusable.
            "expiry": EXPIRY_DATE,
            "strike_price": 24_700.0,
            "underlying_spot_price": SPOT,
            "call_options": _leg(0.0, 76.00, 75.10, 15_000, 120_000, 0, 200),
            "put_options": _leg(262.30, 266.00, 264.00, 45_000, 350_000, 400, 380),
        },
    ],
}

N_RAW = 12  # 6 strikes x (CE + PE); the junk row contributes nothing
N_BAD = 2  # one crossed book + one zero-bid wing
EXPECTED_ORDER = [
    (24_200.0, OptionType.CALL),
    (24_200.0, OptionType.PUT),
    (24_300.0, OptionType.CALL),
    (24_300.0, OptionType.PUT),
    (24_400.0, OptionType.CALL),
    (24_400.0, OptionType.PUT),
    (24_500.0, OptionType.CALL),
    (24_500.0, OptionType.PUT),
    (24_600.0, OptionType.CALL),
    (24_600.0, OptionType.PUT),
    (24_700.0, OptionType.CALL),
    (24_700.0, OptionType.PUT),
]


@pytest.fixture
def patched_fetch(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch fetch_live_option_chain where capture_service imports it; record calls."""
    calls: dict = {}

    def fake_fetch(instrument_key: str, expiry_date: str, access_token: str) -> list[dict]:
        calls["instrument_key"] = instrument_key
        calls["expiry_date"] = expiry_date
        calls["access_token"] = access_token
        # The real function returns payload["data"] (the row list).
        return UPSTOX_CHAIN_FIXTURE["data"]

    monkeypatch.setattr(capture_service, "fetch_live_option_chain", fake_fetch)
    return calls


def _source(rate: float | None = 0.07, now_epoch: float = NOW_EPOCH) -> UpstoxCaptureSource:
    return UpstoxCaptureSource(
        access_token="tok-123",
        instrument_key="NSE_INDEX|Nifty 50",
        expiry_date=EXPIRY_DATE,
        rate=rate,
        now_fn=lambda: now_epoch,
    )


class TestUpstoxCaptureSourceMapping:
    def test_chain_metadata_and_fetch_args(self, patched_fetch: dict) -> None:
        chain = _source().fetch_chain("NIFTY")
        assert patched_fetch == {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "expiry_date": EXPIRY_DATE,
            "access_token": "tok-123",
        }
        assert chain.underlying == "NIFTY"
        assert chain.spot == SPOT
        assert chain.rate == 0.07
        assert chain.timestamp == NOW_EPOCH
        assert len(chain.quotes) == N_RAW

    def test_deterministic_ordering(self, patched_fetch: dict) -> None:
        chain = _source().fetch_chain("NIFTY")
        assert [(q.strike, q.option_type) for q in chain.quotes] == EXPECTED_ORDER

    def test_standard_key_field_values(self, patched_fetch: dict) -> None:
        chain = _source().fetch_chain("NIFTY")
        by_key = {(q.strike, q.option_type): q for q in chain.quotes}
        call = by_key[(24_200.0, OptionType.CALL)]
        assert (call.bid, call.ask, call.ltp) == (331.55, 336.00, 334.10)
        assert (call.volume, call.open_interest) == (120_000, 900_000)
        assert (call.bid_qty, call.ask_qty) == (750, 600)
        put = by_key[(24_200.0, OptionType.PUT)]
        assert (put.bid, put.ask, put.ltp) == (41.50, 42.85, 42.10)
        assert (put.volume, put.open_interest) == (340_000, 2_100_000)

    def test_alias_key_field_values_and_qty_defaults(self, patched_fetch: dict) -> None:
        chain = _source().fetch_chain("NIFTY")
        by_key = {(q.strike, q.option_type): q for q in chain.quotes}
        call = by_key[(24_300.0, OptionType.CALL)]  # bid/best_ask_price/close_price/vol aliases
        assert (call.bid, call.ask, call.ltp) == (262.00, 265.40, 263.70)
        assert (call.volume, call.open_interest) == (98_000, 750_000)
        assert (call.bid_qty, call.ask_qty) == (0, 0)  # depth absent -> 0
        ce_alias = by_key[(24_600.0, OptionType.CALL)]  # leg under "CE"
        assert (ce_alias.bid, ce_alias.ask) == (121.05, 123.20)

    def test_ltp_age_always_zero(self, patched_fetch: dict) -> None:
        # Upstox does not expose last-trade age; the adapter reports 0.0 (fresh).
        chain = _source().fetch_chain("NIFTY")
        assert all(q.ltp_age_seconds == 0.0 for q in chain.quotes)

    def test_missing_spot_raises_data_quality_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = copy.deepcopy(UPSTOX_CHAIN_FIXTURE["data"])
        for row in rows:
            row.pop("underlying_spot_price", None)
        monkeypatch.setattr(
            capture_service, "fetch_live_option_chain", lambda *args, **kwargs: rows
        )
        with pytest.raises(DataQualityError, match="underlying_spot_price"):
            _source().fetch_chain("NIFTY")

    def test_bad_expiry_format_raises_data_quality_error(self) -> None:
        with pytest.raises(DataQualityError, match="YYYY-MM-DD"):
            UpstoxCaptureSource(
                access_token="tok", instrument_key="NSE_INDEX|Nifty 50", expiry_date="27-08-2026"
            )

    def test_default_rate_comes_from_settings(self, patched_fetch: dict) -> None:
        from options_trading.config.settings import settings

        chain = _source(rate=None).fetch_chain("NIFTY")
        assert chain.rate == settings.risk_free_rate


class TestExpiryYearFraction:
    def test_act_365_from_injected_clock(self, patched_fetch: dict) -> None:
        # NOW_EPOCH is exactly 5 days before the 15:30 IST expiry close.
        chain = _source().fetch_chain("NIFTY")
        expected = 5.0 / 365.0
        assert all(q.expiry == pytest.approx(expected, abs=1e-12) for q in chain.quotes)

    def test_intraday_floor_after_close(self, patched_fetch: dict) -> None:
        # One hour past expiry close: raw fraction is negative, floor kicks in.
        chain = _source(now_epoch=EXPIRY_EPOCH + 3600.0).fetch_chain("NIFTY")
        assert all(q.expiry == MIN_EXPIRY_YEARS for q in chain.quotes)


class TestCaptureAndStore:
    def test_end_to_end_stores_only_clean_quotes(self, patched_fetch: dict, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        report = capture_and_store(_source(), store, "NIFTY")

        path = Path(report.path)
        assert path.exists() and path.suffix == ".parquet"
        assert report.n_raw == N_RAW
        assert report.n_clean == N_RAW - N_BAD
        assert report.rejection_stats == {
            "crossed_book": 1,
            "stale_quote": 0,
            "wide_spread": 0,
            "zero_bid_wing": 1,
            "non_positive_mid": 0,
        }
        assert report.spot == SPOT
        assert report.timestamp == NOW_EPOCH

        reread = store.read(path)
        assert len(reread.quotes) == N_RAW - N_BAD
        # The planted bad quotes must not be in the stored history.
        assert all(q.bid > 0.0 for q in reread.quotes)  # zero-bid wing gone
        assert all(q.bid <= q.ask for q in reread.quotes)  # crossed book gone
        assert (24_400.0, OptionType.CALL) not in {(q.strike, q.option_type) for q in reread.quotes}
        # Chain metadata preserved verbatim.
        assert reread.underlying == "NIFTY"
        assert reread.spot == SPOT
        assert reread.rate == 0.07
        assert reread.timestamp == NOW_EPOCH
        assert store.list_snapshots("NIFTY") == [path]


class TestCaptureRoutes:
    @pytest.fixture
    def app(self):
        from options_trading.main import create_app

        application = create_app()
        yield application
        application.dependency_overrides.clear()

    @pytest.fixture
    def client(self, app) -> TestClient:
        # No context manager: lifespan (real AuthService startup) must not run.
        return TestClient(app)

    def test_run_returns_report_json(
        self, app, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        app.dependency_overrides[capture_routes.get_access_token] = lambda: "route-token"
        monkeypatch.setattr(capture_routes.settings, "snapshot_store_path", str(tmp_path))

        seen: dict = {}
        fake_report = CaptureReport(
            path=str(tmp_path / "NIFTY" / "2026-08-22" / "093000.parquet"),
            n_raw=N_RAW,
            n_clean=N_RAW - N_BAD,
            rejection_stats={"crossed_book": 1, "zero_bid_wing": 1},
            spot=SPOT,
            timestamp=NOW_EPOCH,
        )

        def fake_capture_and_store(source, store, underlying, config=None):
            seen["source"] = source
            seen["store"] = store
            seen["underlying"] = underlying
            return fake_report

        monkeypatch.setattr(capture_routes, "capture_and_store", fake_capture_and_store)

        resp = client.post(
            "/api/v1/capture/run",
            json={
                "underlying": "NIFTY",
                "instrument_key": "NSE_INDEX|Nifty 50",
                "expiry_date": EXPIRY_DATE,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["n_raw"] == N_RAW
        assert body["n_clean"] == N_RAW - N_BAD
        assert body["rejection_stats"] == {"crossed_book": 1, "zero_bid_wing": 1}
        assert body["spot"] == SPOT
        assert body["path"].endswith(".parquet")
        assert seen["underlying"] == "NIFTY"
        assert isinstance(seen["source"], UpstoxCaptureSource)
        assert isinstance(seen["store"], SnapshotStore)

    def test_run_maps_data_quality_error_to_422(
        self, app, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        app.dependency_overrides[capture_routes.get_access_token] = lambda: "route-token"
        monkeypatch.setattr(capture_routes.settings, "snapshot_store_path", str(tmp_path))

        def failing_capture(source, store, underlying, config=None):
            raise DataQualityError("no spot anywhere")

        monkeypatch.setattr(capture_routes, "capture_and_store", failing_capture)

        resp = client.post(
            "/api/v1/capture/run",
            json={
                "underlying": "NIFTY",
                "instrument_key": "NSE_INDEX|Nifty 50",
                "expiry_date": EXPIRY_DATE,
            },
        )
        assert resp.status_code == 422
        assert "no spot anywhere" in resp.json()["detail"]

    def test_run_unauthenticated_is_401(self, app, client: TestClient) -> None:
        class NoAuth:
            async def __aenter__(self) -> NoAuth:
                return self

            async def __aexit__(self, *exc: object) -> bool:
                return False

            async def get_valid_access_token(self, user_id: str = "default") -> str:
                raise RuntimeError("no stored token")

        app.dependency_overrides[dependencies.get_auth_service] = lambda: NoAuth()

        resp = client.post(
            "/api/v1/capture/run",
            json={
                "underlying": "NIFTY",
                "instrument_key": "NSE_INDEX|Nifty 50",
                "expiry_date": EXPIRY_DATE,
            },
        )
        assert resp.status_code == 401

    def test_history_lists_snapshots(
        self, app, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(capture_routes.settings, "snapshot_store_path", str(tmp_path))
        store = SnapshotStore(tmp_path)
        written = store.write(
            RawChain(
                underlying="NIFTY",
                spot=SPOT,
                rate=0.07,
                timestamp=NOW_EPOCH,
                quotes=(
                    RawQuote(
                        strike=24_500.0,
                        expiry=5.0 / 365.0,
                        option_type=OptionType.CALL,
                        bid=180.55,
                        ask=183.40,
                        ltp=182.00,
                        volume=250_000,
                        open_interest=1_800_000,
                    ),
                ),
            )
        )

        resp = client.get("/api/v1/capture/history/NIFTY")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"underlying": "NIFTY", "count": 1, "snapshots": [str(written)]}

        empty = client.get("/api/v1/capture/history/BANKNIFTY")
        assert empty.status_code == 200
        assert empty.json()["count"] == 0
