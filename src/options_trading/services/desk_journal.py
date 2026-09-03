"""Turns the desk journal into a readable decision trail.

The journal is the product's most defensible artefact: every fill, every
rejection and every deliberation is already on disk with a sequence number
and a correlation id (ADR-009). What it is not, in raw form, is legible — one
cycle is seven-plus events of nested payloads, and dumping them is not the
same as explaining them.

This module reads one cycle's correlation group and renders it as an ordered
narrative: what the market looked like, what the strategy proposed, what the
panel concluded and by what margin, which risk check bound and at what cap,
what actually filled, and what the hedger decided. Rejections keep the
engine's own wording rather than a paraphrase, because that wording names
the breached limit and its cap — which is what tells a reader whether to
widen a limit or fix the signal.

Nothing here recomputes a decision. If a number is displayed it was read
from the journal; the trail is a view, never a second opinion.
"""

from __future__ import annotations

import logging
from typing import Any

from optitrade.journal.event_log import EventLog

logger = logging.getLogger(__name__)

#: Events that belong to one cycle's correlation group, in narrative order.
#: A cycle that never deliberated simply has no ``debate_decision`` entry.
_STAGE_ORDER = {
    "market_features": 0,
    "debate_decision": 1,
    "risk_decision": 2,
    "order_rejected": 3,
    "kill_switch_engaged": 4,
    "hedge_decision": 5,
    "daily_cycle": 6,
}


def _stage_rank(event_type: str) -> int:
    return _STAGE_ORDER.get(event_type, len(_STAGE_ORDER))


def _debate_entry(data: dict[str, Any]) -> dict[str, Any]:
    """One deliberation, with the per-expert opinions kept intact."""
    return {
        "kind": "debate",
        "proposal": data.get("proposal"),
        "consensus": data.get("consensus"),
        "approvalScore": data.get("approval_score"),
        "rationale": data.get("rationale"),
        "dissenters": list(data.get("dissenters", []) or []),
        "opinions": [
            {
                "expert": op.get("expert"),
                "stance": op.get("stance"),
                "assessment": op.get("assessment"),
                "concerns": list(op.get("concerns", []) or []),
                "confidence": op.get("confidence"),
            }
            for op in data.get("opinions", []) or []
        ],
    }


def _risk_entry(data: dict[str, Any]) -> dict[str, Any]:
    """One risk review. Every check is kept, not just the binding one.

    The engine runs the full battery without short-circuiting precisely so a
    rejection arrives with the whole report; collapsing it to the first
    breach here would throw that away.
    """
    results = data.get("results", []) or []
    binding = [r for r in results if r.get("verdict") != "approve"]
    return {
        "kind": "risk",
        "order": data.get("order"),
        "verdict": data.get("verdict"),
        "adjustedQuantity": data.get("adjusted_quantity"),
        "checks": [
            {
                "check": r.get("check"),
                "verdict": r.get("verdict"),
                "reason": r.get("reason"),
                "allowedQuantity": r.get("allowed_quantity"),
            }
            for r in results
        ],
        # The reason a reader actually wants, promoted out of the list.
        "bindingReasons": [r.get("reason") for r in binding if r.get("reason")],
    }


def _rejection_entry(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "rejection",
        "order": data.get("order"),
        "stage": data.get("stage"),
        "reason": data.get("reason"),
    }


def _hedge_entry(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "hedge",
        "action": data.get("action"),
        "order": data.get("order"),
        "portfolioDelta": data.get("portfolio_delta"),
        "bandHalfWidth": data.get("band_half_width"),
        "bandScale": data.get("band_scale"),
        "rationale": data.get("rationale"),
        "confidence": data.get("confidence"),
    }


def _market_entry(data: dict[str, Any]) -> dict[str, Any]:
    known = {"ts", "spot", "realized_vol"}
    return {
        "kind": "market",
        "spot": data.get("spot"),
        "realizedVol": data.get("realized_vol"),
        # Feature keys are producer-defined (strategy/base.py), so they are
        # passed through rather than enumerated here.
        "features": {k: v for k, v in data.items() if k not in known},
    }


def _halt_entry(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "halt",
        "reason": data.get("reason"),
        "cancelledOrders": data.get("cancelled_orders"),
    }


_BUILDERS = {
    "market_features": _market_entry,
    "debate_decision": _debate_entry,
    "risk_decision": _risk_entry,
    "order_rejected": _rejection_entry,
    "kill_switch_engaged": _halt_entry,
    "hedge_decision": _hedge_entry,
}


def build_decision_trail(journal: EventLog, correlation_id: str) -> dict[str, Any]:
    """The ordered decision trail for one cycle, ready for the wire.

    Returns a payload whose ``found`` is ``False`` when the correlation id
    has no events — a journal that was rotated, or an id from a previous run.
    That is reported rather than rendered as an empty (and therefore
    uneventful-looking) day.
    """
    try:
        events = journal.events_by_correlation(correlation_id)
    except Exception as exc:
        # A corrupt journal must not blank the desk tab; say so instead.
        logger.exception("Journal replay failed for correlation %s", correlation_id)
        return {
            "found": False,
            "correlationId": correlation_id,
            "reason": f"the journal could not be replayed: {type(exc).__name__}: {exc}",
            "steps": [],
            "summary": None,
        }
    if not events:
        return {
            "found": False,
            "correlationId": correlation_id,
            "reason": (
                "no journal events carry this correlation id — the cycle predates "
                "the current journal file, which rotates per desk run"
            ),
            "steps": [],
            "summary": None,
        }

    # Sequence is the tie-break, not the primary key: the narrative order is
    # the pipeline order, and two orders in one cycle interleave debate and
    # risk events that a pure sequence sort would read correctly but a reader
    # scanning for "what did risk say" would not.
    ordered = sorted(events, key=lambda e: (_stage_rank(e.event_type), e.sequence))
    steps: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    for event in ordered:
        if event.event_type == "daily_cycle":
            summary = dict(event.data)
            continue
        builder = _BUILDERS.get(event.event_type)
        if builder is None:
            continue
        entry = builder(dict(event.data))
        entry["sequence"] = event.sequence
        entry["timestamp"] = event.timestamp
        steps.append(entry)

    return {
        "found": True,
        "correlationId": correlation_id,
        "reason": None,
        "steps": steps,
        "summary": summary,
    }


__all__ = ["build_decision_trail"]
