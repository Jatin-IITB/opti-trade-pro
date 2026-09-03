"""The React app and the API are served from one origin.

Before this, the product spanned two ports and three UIs: the React app on the
Vite dev server, a JSON blurb at ``/``, and a server-rendered ``/dashboard``
that loaded a hardcoded data object and random-walked the spot so it animated
like a live feed. OAuth redirected to that last one, so the first screen after
connecting a broker showed a fabricated P&L.

The fake dashboard is deleted. FastAPI now serves ``frontend/dist`` at the
root, which means one port, no CORS, and a session cookie that does not depend
on a dev proxy forwarding it.

The invariant these tests protect: mounting a SPA at ``/`` must not swallow the
API. A catch-all that returns ``index.html`` for an unknown ``/api`` path turns
every backend 404 into a 200 full of HTML, which a fetch() then fails to parse
in a way that looks like a data bug rather than a routing bug.
"""

import pytest
from fastapi.testclient import TestClient

import options_trading.main as main_module

pytestmark = pytest.mark.unit


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_module.app)


class TestApiIsNotShadowed:
    def test_unknown_api_path_404s_as_json(self, client):
        """Not the SPA. This is the whole risk of mounting at the root."""
        resp = client.get("/api/v1/no-such-endpoint")

        assert resp.status_code == 404
        assert not resp.headers["content-type"].startswith("text/html")

    @pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
    def test_operational_endpoints_still_resolve(self, client, path):
        assert client.get(path).status_code == 200

    def test_api_routes_are_registered(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert sum(1 for p in paths if p.startswith("/api/v1")) > 20


class TestLegacyDashboardIsGone:
    def test_fabricated_dashboard_page_is_removed(self, client):
        """It never called an API; it rendered a hardcoded object."""
        assert "/dashboard" not in client.get("/openapi.json").json()["paths"]

    def test_legacy_assets_are_removed(self):
        from pathlib import Path

        package = Path(main_module.__file__).parent
        assert not (package / "templates").exists()
        assert not (package / "static").exists()

    def test_login_no_longer_returns_to_it(self):
        """OAuth landed the user on the fabricated page; now on the real app."""
        from options_trading.api.routes.auth import _DEFAULT_RETURN_URL, safe_return_url

        assert _DEFAULT_RETURN_URL == "/"
        assert safe_return_url(None) == "/"
        # The open-redirect guard still holds for the new default.
        assert safe_return_url("//evil.example") == "/"
        assert safe_return_url("https://evil.example") == "/"


class TestFrontendServing:
    def test_root_serves_the_built_app(self, client):
        resp = client.get("/")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_missing_build_is_reported_not_silently_blank(self, tmp_path, monkeypatch):
        """A backend-only checkout must say the build is missing.

        Serving nothing would read as a broken backend, when the fix is a
        frontend build rather than a server restart. 503 because the API is up
        and only the UI is absent.
        """
        monkeypatch.setattr(main_module, "frontend_dist_path", lambda: tmp_path / "absent")

        resp = TestClient(main_module.create_app()).get("/")

        assert resp.status_code == 503
        assert "npm run build" in resp.text

    def test_api_still_works_without_a_frontend_build(self, tmp_path, monkeypatch):
        """Backend-only deployments are a supported shape."""
        monkeypatch.setattr(main_module, "frontend_dist_path", lambda: tmp_path / "absent")

        assert TestClient(main_module.create_app()).get("/health").status_code == 200
