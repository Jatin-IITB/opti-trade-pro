"""Paper-desk endpoints: state, cycle history, decision trail, kill switch.

**No endpoint here places an order.** ``POST /desk/advance`` runs
:func:`optitrade.desk.cycle.run_daily_cycle`, whose fills are simulated
inside the quant core against a replayed snapshot. This module imports no
broker client and the app's Upstox connection is read-only; that is asserted
by ``tests/unit/test_desk_routes.py::TestPaperOnlyInvariant``.

The kill switch is deliberately asymmetric. Engaging is one unauthenticated-
by-nothing-extra POST with an optional reason, because a person trying to
stop a desk should never be slowed by a form. Resetting requires the caller
to echo a fixed confirmation phrase *and* state a reason, because resuming
trading is the decision that deserves the friction (ADR-008).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from optitrade.data import SnapshotStore
from optitrade.desk import KillSwitch

from ...config.settings import settings
from ...services.desk_service import DeskService, desk_config_from_settings
from ...services.desk_state_store import DeskStateStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desk", tags=["Paper Desk"])

#: The caller must echo this exactly to clear a halt. A fixed phrase rather
#: than a boolean flag: `{"confirm": true}` is a plausible default for a
#: script, `RESET` is not something a client sends by accident.
RESET_CONFIRMATION = "RESET"


class KillSwitchEngageRequest(BaseModel):
    reason: str = Field(
        default="engaged from the dashboard",
        max_length=500,
        description="Why the desk is being halted; recorded in the marker file and the journal",
    )


class KillSwitchResetRequest(BaseModel):
    confirm: str = Field(
        description=f"Must be exactly {RESET_CONFIRMATION!r} to clear the halt",
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Why trading may resume; journaled so a resume is auditable",
    )


def get_desk_service(request: Request) -> DeskService:
    """Resolve the app-wide :class:`DeskService`.

    One instance per app: it serialises advances with an internal lock, so a
    second instance would let two concurrent advances start from the same
    book and double the fills. Built lazily here only so the routes remain
    usable in tests that construct a bare app.
    """
    service = getattr(request.app.state, "desk_service", None)
    if service is None:
        logger.warning("desk_service missing from app.state; created one lazily")
        service = DeskService(
            SnapshotStore(Path(settings.snapshot_store_path)),
            DeskStateStore(Path(settings.desk_state_path)),
            Path(settings.desk_journal_dir),
            KillSwitch(Path(settings.desk_kill_switch_path)),
            desk_config_from_settings(),
        )
        request.app.state.desk_service = service
    return service


@router.get("/state")
async def get_desk_state(request: Request) -> dict[str, Any]:
    """The desk as it stands: kill switch, cycle history, book, account.

    Cheap and side-effect free. A desk that has never run reports that in
    ``history.hasHistory`` rather than returning an empty cycle list that
    would render as a flat book.
    """
    service = get_desk_service(request)
    payload = await service.build_async()
    return payload.to_wire_dict()


@router.post("/advance")
async def advance_desk(request: Request) -> dict[str, Any]:
    """Run the desk over every captured day it has not processed yet.

    PAPER ONLY: fills are simulated in the quant core. Idempotent — a second
    call with no new captured days does nothing and says so in ``warnings``.
    Refuses to run while the kill switch is engaged.
    """
    service = get_desk_service(request)
    payload = await service.advance_async()
    return payload.to_wire_dict()


@router.get("/kill-switch")
async def get_kill_switch(request: Request) -> dict[str, Any]:
    """Current halt state, read from the marker file on every call."""
    return get_desk_service(request).kill_switch_state()


@router.post("/kill-switch/engage")
async def engage_kill_switch(
    request: Request, body: KillSwitchEngageRequest | None = None
) -> dict[str, Any]:
    """Halt the desk immediately.

    Takes no confirmation by design: stopping is always safe, and anything
    that delays a halt is a defect. An empty body is accepted so the control
    works even if a client cannot serialise one.
    """
    payload = body or KillSwitchEngageRequest()
    service = get_desk_service(request)
    state = service.engage_kill_switch(payload.reason)
    logger.warning("Paper desk halted via API: %s", payload.reason)
    return state


@router.post("/kill-switch/reset")
async def reset_kill_switch(request: Request, body: KillSwitchResetRequest) -> dict[str, Any]:
    """Clear the halt. Requires the confirmation phrase and a stated reason."""
    if body.confirm != RESET_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"resetting the kill switch requires confirm={RESET_CONFIRMATION!r}; "
                "the desk stays halted"
            ),
        )
    service = get_desk_service(request)
    state = service.reset_kill_switch(body.reason)
    logger.warning("Paper desk kill switch reset via API: %s", body.reason)
    return state


@router.get("/trail/{correlation_id}")
async def get_decision_trail(request: Request, correlation_id: str) -> dict[str, Any]:
    """The full decision trail for one cycle: market, debate, risk, hedge.

    Returns 200 with ``found: false`` rather than 404 when the id has no
    events: the cycle is real (it is in the desk's history) and the missing
    piece is the journal, which is a different fact from "no such cycle".
    """
    service = get_desk_service(request)
    return service.decision_trail(correlation_id)


__all__ = ["RESET_CONFIRMATION", "get_desk_service", "router"]
