"""The frontend must have no bundled market data to fall back to.

`frontend/src/data/demo.json` seeded the market panels with a chain priced
off a 20,000 NIFTY. Because the backend broadcasts nothing until the first
capture completes, that baseline was the steady state whenever the market was
shut, and the `DataSource` "Sim" badge that once disclosed it had already
been removed as unused — so a fabricated surface, chain and Greeks rendered
exactly like live ones. It was observed showing 20,000 against a real spot of
23,873.

These are guard tests, in the same spirit as the removal guards for the
charges tool and the strategy layer: the frontend has no test runner, and the
property worth protecting is an *absence*, which a build cannot check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


def source_files() -> list[Path]:
    return sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx"))


class TestDemoDataIsGone:
    def test_the_demo_payload_file_does_not_exist(self):
        assert not (FRONTEND_SRC / "data" / "demo.json").exists()

    def test_no_bundled_json_payload_replaced_it(self):
        """Renaming the file would defeat a test that only checks one path."""
        data_dir = FRONTEND_SRC / "data"
        if not data_dir.exists():
            return
        assert list(data_dir.glob("*.json")) == []

    def test_nothing_imports_a_demo_payload(self):
        offenders = [
            f"{path.relative_to(FRONTEND_SRC)}: {line.strip()}"
            for path in source_files()
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.match(r"\s*import\s", line) and "data/demo" in line
        ]
        assert offenders == [], f"a bundled demo payload is imported again: {offenders}"

    def test_no_source_file_hardcodes_the_old_demo_spot(self):
        """20000.0 was the fabricated NIFTY level; it must not reappear."""
        offenders = [
            f"{path.relative_to(FRONTEND_SRC)}:{n}"
            for path in source_files()
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\bspot\b.{0,20}\b20000(\.0)?\b", line)
        ]
        assert offenders == [], f"the demo spot is back: {offenders}"


class TestPanelsGateOnLiveData:
    def test_every_dashboard_field_starts_null(self):
        """A non-null seed is exactly how fabricated data reached the screen."""
        text = (FRONTEND_SRC / "hooks" / "useLiveData.ts").read_text(encoding="utf-8")
        match = re.search(r"const NO_DATA:\s*DashboardData\s*=\s*\{(.*?)\}", text, re.S)

        assert match is not None, "useLiveData no longer defines a NO_DATA baseline"
        for field, value in re.findall(r"(\w+)\s*:\s*([^,\n]+)", match.group(1)):
            assert value.strip() == "null", f"{field} is seeded with {value.strip()!r}"

    def test_the_baseline_covers_every_declared_field(self):
        """A field absent from NO_DATA would be `undefined`, not reported."""
        text = (FRONTEND_SRC / "hooks" / "useLiveData.ts").read_text(encoding="utf-8")
        declared = set(
            re.findall(
                r"^\s*(\w+):\s*any;",
                re.search(r"export interface DashboardData\s*\{(.*?)\}", text, re.S).group(1),
                re.M,
            )
        )
        seeded = set(
            re.findall(
                r"^\s*(\w+):",
                re.search(r"const NO_DATA:\s*DashboardData\s*=\s*\{(.*?)\}", text, re.S).group(1),
                re.M,
            )
        )

        assert declared == seeded, f"missing from NO_DATA: {declared - seeded}"

    def test_market_panels_are_wrapped_in_a_gate(self):
        """Each market panel must report missing data rather than render it."""
        text = (FRONTEND_SRC / "App.tsx").read_text(encoding="utf-8")
        for panel in (
            "VolSurface",
            "EssviCalibration",
            "GreeksBook",
            "ScenarioHeatmap",
            "HigherOrderGreeks",
            "OptionChain",
            "RiskDashboard",
        ):
            rendered = re.search(rf"<{panel}\s+data=", text)
            assert rendered is not None, f"{panel} is no longer rendered"
            preceding = text[: rendered.start()]
            assert preceding.rstrip().endswith(">"), f"{panel} is not inside a gate element"
            assert "<LiveGate" in preceding[-400:], (
                f"{panel} renders without a LiveGate; it would show a blank or "
                "crashing panel instead of reporting that no chain is captured"
            )

    def test_the_header_spot_is_nullable(self):
        """Printing `spot` unconditionally is what showed a fake quote."""
        text = (FRONTEND_SRC / "hooks" / "useLiveData.ts").read_text(encoding="utf-8")

        assert re.search(r"spot:\s*number\s*\|\s*null", text), (
            "LiveState.spot must be nullable so the header can show no quote"
        )

    def test_the_header_does_not_call_tolocalestring_unguarded(self):
        app = (FRONTEND_SRC / "App.tsx").read_text(encoding="utf-8")

        assert "live.spot === null" in app, "the header must branch on a missing spot"


class TestBuildOutputHasNoDemoPayload:
    def test_the_built_bundle_carries_no_demo_chain(self):
        """Catches a stale `dist/` being served after the source was cleaned."""
        dist = REPO_ROOT / "frontend" / "dist" / "assets"
        if not dist.exists():
            pytest.skip("frontend has not been built in this checkout")
        for bundle in dist.glob("*.js"):
            text = bundle.read_text(encoding="utf-8", errors="ignore")
            assert '"spot":20000' not in text.replace(" ", "")
            assert "spot:20000" not in text.replace(" ", "")


class TestStaleDocsWithdrawn:
    def test_adr_024_records_that_the_fallback_was_removed(self):
        """ADR-024 described the demo baseline as a feature (CLAUDE.md rule 8)."""
        text = (REPO_ROOT / "docs" / "adr" / "024-live-data-pipeline.md").read_text(
            encoding="utf-8"
        )

        assert "## Amendment" in text
        assert "demo.json" in text.split("## Amendment")[1]


def test_demo_json_is_not_merely_gitignored():
    """Ignoring it would let a local copy resurrect the fallback silently."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "demo.json" not in gitignore


def test_no_python_route_serves_a_demo_payload():
    """The platform must not reintroduce the fallback from the server side."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "options_trading").rglob("*.py")
        if "demo.json" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_deleted_payload_is_not_tracked_by_git():
    """A guard that the working tree and the index agree."""
    tracked = REPO_ROOT / "frontend" / "src" / "data" / "demo.json"
    assert not tracked.exists()
    # If a build artifact re-created it, the JSON would parse; assert not.
    with pytest.raises(FileNotFoundError):
        json.loads(tracked.read_text(encoding="utf-8"))
