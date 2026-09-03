"""Analyst endpoint: deterministic analyst reports over the desk journal.

Read-only in the strongest sense available here — the endpoint replays the
journal and audits claims against it, and appends nothing. That is why the
roster excludes ``RiskOfficerAnalyst``, whose ``answer`` journals the scenario
query it cites; see :data:`~options_trading.services.analyst_service._EXCLUDED`.

No endpoint here places an order, and this module imports no broker client.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ...config.settings import settings
from ...services.analyst_service import AnalystService, analyst_config_from_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysts", tags=["Analysts"])


def get_analyst_service(request: Request) -> AnalystService:
    """Resolve the app-wide :class:`AnalystService`.

    One instance per app so the cache and the shared in-flight task are
    shared; a per-request instance would re-audit the whole journal on every
    call. Built lazily here only so the route stays usable in tests that
    construct a bare app.
    """
    service = getattr(request.app.state, "analyst_service", None)
    if service is None:
        logger.warning("analyst_service missing from app.state; created one lazily")
        service = AnalystService(
            Path(settings.desk_journal_dir),
            analyst_config_from_settings(),
        )
        request.app.state.analyst_service = service
    return service


@router.get("/report")
async def get_analyst_report(request: Request) -> dict[str, Any]:
    """Analyst reports with a groundedness verdict on every claim.

    Always 200. A journal with nothing to report on is a fact about the desk,
    not a server error, and it arrives as ``hasJournal: false`` with a reason
    the panel renders. Analysts that could not report are listed in
    ``failures`` with the event type each needed — partial coverage is the
    normal case, since each analyst reads one event type.
    """
    payload = await get_analyst_service(request).build_async()
    return payload.to_wire_dict()


__all__ = ["get_analyst_service", "router"]
