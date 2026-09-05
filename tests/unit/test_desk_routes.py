"""Tests for the paper-desk REST routes.

Includes the hard safety invariant this whole feature is built around: the
application has no order-placement path, and adding the desk must not create
one. ``TestPaperOnlyInvariant`` asserts that against the source tree rather
than against a mock, because the property being protected is "nothing in this
package can send an order", which no single call site can demonstrate.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.desk import RESET_CONFIRMATION
from options_trading.api.routes.desk import router as desk_router
from options_trading.services.desk_service import DeskService, DeskServiceConfig
from options_trading.services.desk_state_store import DeskStateStore
from optitrade.data import SnapshotStore, SyntheticSource
from optitrade.data.models import RawChain
from optitrade.desk import KillSwitch

pytestmark = pytest.mark.unit

UNDERLYING = "NIFTY"
BASE_TIMESTAMP = 1_700_000_000.0
SECONDS_PER_DAY = 86_400.0
SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"


def build_service(tmp_path: pathlib.Path, n_days: int) -> DeskService:
    store = SnapshotStore(tmp_path / "snapshots")
    source = SyntheticSource(seed=1)
    for i in range(n_days):
        chain = source.fetch_chain(UNDERLYING)
        store.write(
            RawChain(
                underlying=chain.underlying,
                spot=chain.spot * (1 + 0.0002 * ((i % 3) - 1)),
                rate=chain.rate,
                timestamp=BASE_TIMESTAMP + i * SECONDS_PER_DAY,
                quotes=chain.quotes,
                dividend_yield=chain.dividend_yield,
            )
        )
    return DeskService(
        store,
        DeskStateStore(tmp_path / "desk_state.json"),
        tmp_path / "journal",
        KillSwitch(tmp_path / "HALT"),
        DeskServiceConfig(underlying=UNDERLYING, lot_size=75),
    )


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(desk_router)
    app.state.desk_service = build_service(tmp_path, n_days=6)
    return TestClient(app)


@pytest.fixture()
def empty_client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(desk_router)
    app.state.desk_service = build_service(tmp_path, n_days=0)
    return TestClient(app)


class TestState:
    def test_state_declares_itself_paper(self, client):
        body = client.get("/desk/state").json()

        assert body["isPaper"] is True
        assert body["mode"] == "paper"

    def test_a_never_run_desk_reports_no_history(self, client):
        body = client.get("/desk/state").json()

        assert body["history"]["hasHistory"] is False
        assert body["cycles"] == []

    def test_state_is_side_effect_free(self, client):
        """Reading the desk must never advance it."""
        client.get("/desk/state")
        client.get("/desk/state")

        assert client.get("/desk/state").json()["cycles"] == []


class TestAdvance:
    def test_advance_runs_the_captured_days(self, client):
        body = client.post("/desk/advance").json()

        assert body["history"]["hasHistory"] is True
        assert len(body["cycles"]) == 6

    def test_advance_is_idempotent_over_http(self, client):
        first = client.post("/desk/advance").json()

        second = client.post("/desk/advance").json()

        assert len(second["cycles"]) == len(first["cycles"])
        assert second["book"] == first["book"]

    def test_advance_with_no_data_reports_rather_than_errors(self, empty_client):
        response = empty_client.post("/desk/advance")

        assert response.status_code == 200
        assert response.json()["history"]["hasHistory"] is False


class TestKillSwitchRoutes:
    def test_status_starts_clear(self, client):
        assert client.get("/desk/kill-switch").json()["engaged"] is False

    def test_engage_needs_no_confirmation(self, client):
        """Stopping must never be slowed down."""
        response = client.post("/desk/kill-switch/engage", json={"reason": "vega breach"})

        assert response.status_code == 200
        assert response.json()["engaged"] is True
        assert "vega breach" in response.json()["reason"]

    def test_engage_works_with_no_body_at_all(self, client):
        response = client.post("/desk/kill-switch/engage")

        assert response.status_code == 200
        assert response.json()["engaged"] is True

    def test_an_engaged_desk_refuses_to_advance_over_http(self, client):
        client.post("/desk/kill-switch/engage", json={"reason": "halted"})

        body = client.post("/desk/advance").json()

        assert body["cycles"] == []
        assert any("did not advance" in w for w in body["warnings"])

    def test_reset_without_the_phrase_is_refused(self, client):
        client.post("/desk/kill-switch/engage", json={"reason": "halted"})

        response = client.post("/desk/kill-switch/reset", json={"confirm": "yes", "reason": "ok"})

        assert response.status_code == 400
        assert client.get("/desk/kill-switch").json()["engaged"] is True

    def test_reset_requires_a_reason(self, client):
        client.post("/desk/kill-switch/engage", json={"reason": "halted"})

        response = client.post(
            "/desk/kill-switch/reset", json={"confirm": RESET_CONFIRMATION, "reason": ""}
        )

        assert response.status_code == 422
        assert client.get("/desk/kill-switch").json()["engaged"] is True

    def test_reset_with_the_phrase_and_a_reason_clears_the_halt(self, client):
        client.post("/desk/kill-switch/engage", json={"reason": "halted"})

        response = client.post(
            "/desk/kill-switch/reset",
            json={"confirm": RESET_CONFIRMATION, "reason": "cause fixed"},
        )

        assert response.status_code == 200
        assert response.json()["engaged"] is False


class TestTrailRoute:
    def test_trail_returns_the_decision_steps(self, client):
        cycles = client.post("/desk/advance").json()["cycles"]
        entering = next(c for c in cycles if c["n_fills"] > 0)

        body = client.get(f"/desk/trail/{entering['correlation_id']}").json()

        assert body["found"] is True
        kinds = {step["kind"] for step in body["steps"]}
        assert {"market", "debate", "risk", "hedge"} <= kinds

    def test_an_unknown_id_is_200_with_found_false(self, client):
        """The cycle may be real while the journal entry is gone."""
        response = client.get("/desk/trail/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 200
        assert response.json()["found"] is False
        assert response.json()["reason"]


class TestPaperOnlyInvariant:
    """The application must have no path that places a real order.

    Asserted against the source tree, not a mock: the property is "nothing in
    this package can send an order", which no single call site demonstrates.
    Note the deliberate omission of a bare ``/v2/order`` pattern —
    ``portfolio_client.fetch_orders`` GETs ``/v2/order/retrieve-all`` to read
    the order book, which is exactly the read-only access this app is meant
    to have. The patterns below name *placement*, not the word "order".
    """

    #: Order-mutating verbs and endpoints. Matched as whole words so
    #: unrelated identifiers (``ordering``, ``reorder``) do not trip the guard.
    FORBIDDEN = (
        r"\bplace_order\b",
        r"\bplaceOrder\b",
        r"\bcancel_order\b",
        r"\bmodify_order\b",
        r"\bsquare_off\b",
        r"/order/place",
        r"/order/cancel",
        r"/order/modify",
        r"/order/sell",
        r"/order/buy",
        r"place-order",
        r"/order/multi",
    )

    def _platform_sources(self) -> list[pathlib.Path]:
        return sorted((SRC_ROOT / "options_trading").rglob("*.py"))

    def test_no_order_placement_symbol_exists_in_the_platform(self):
        offenders: list[str] = []
        for path in self._platform_sources():
            text = path.read_text(encoding="utf-8")
            for pattern in self.FORBIDDEN:
                if re.search(pattern, text):
                    offenders.append(f"{path.relative_to(SRC_ROOT)}: {pattern}")
        assert offenders == [], (
            "an order-placement path appeared in options_trading; this app is "
            f"read-only against the broker: {offenders}"
        )

    def test_the_broker_client_exposes_only_read_methods_for_orders(self):
        """Every order-related client method must be a fetch, not a send."""
        text = (SRC_ROOT / "options_trading" / "services" / "portfolio_client.py").read_text(
            encoding="utf-8"
        )
        order_methods = re.findall(r"def\s+(\w*order\w*)\s*\(", text, flags=re.IGNORECASE)

        assert order_methods, "expected at least fetch_orders to exist"
        for name in order_methods:
            assert name.startswith(("fetch_", "_parse_")), (
                f"portfolio_client.{name} is order-related but is not a read; "
                "the broker connection must stay read-only"
            )

    def test_the_desk_modules_import_no_broker_or_http_client(self):
        """The desk reaches the market only through the Parquet store.

        Checked on import statements rather than raw text: these modules
        discuss the broker in prose precisely to record that they never call
        it, and a substring search would forbid saying so.
        """
        for name in (
            "services/desk_service.py",
            "services/desk_state_store.py",
            "services/desk_journal.py",
            "api/routes/desk.py",
        ):
            text = (SRC_ROOT / "options_trading" / name).read_text(encoding="utf-8")
            imports = [
                line.strip() for line in text.splitlines() if re.match(r"\s*(import|from)\s", line)
            ]
            for line in imports:
                assert "httpx" not in line, f"{name} imports an HTTP client: {line}"
                assert "requests" not in line, f"{name} imports an HTTP client: {line}"
                assert "upstox" not in line.lower(), f"{name} imports a broker module: {line}"
                assert "portfolio_client" not in line, f"{name} imports the broker client: {line}"

    def test_the_quant_core_desk_has_no_platform_or_network_imports(self):
        """ADR-002 one-way dependency, and the core does no I/O to a broker."""
        for path in sorted((SRC_ROOT / "optitrade" / "desk").rglob("*.py")):
            imports = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if re.match(r"\s*(import|from)\s", line)
            ]
            for line in imports:
                assert "options_trading" not in line, f"{path.name} imports the platform: {line}"
                assert "httpx" not in line, f"{path.name} does network I/O: {line}"
                assert "requests" not in line, f"{path.name} does network I/O: {line}"
