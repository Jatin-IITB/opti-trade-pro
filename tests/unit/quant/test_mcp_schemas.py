"""The agent-facing JSON schema must name its fields.

A tool parameter annotated ``dict[str, Any]`` serialises to ``{"type":
"object"}`` with no properties. An agent reading that has no way to learn the
field names, guesses, and gets back an opaque "Error executing tool" because
the MCP layer swallows the ``TypeError``. ``review_order`` — the tool guarding
the money path — shipped that way, and no unit test caught it: the existing
tests call ``build_tools()`` in Python with correct kwargs, never through the
schema an agent actually reads.

Two guards here:

- **Regression**: no tool may expose a property-less object parameter. This
  catches the original bug for any tool added later, not just today's six.
- **Drift**: each ``TypedDict`` mirrors an engine dataclass, so a new field on
  ``RiskLimits`` or ``VRPConfig`` fails here rather than silently going missing
  from the schema. Duplication is the price of a self-describing schema without
  adding pydantic to the quant core; this test is what makes the price safe.
"""

from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from optitrade.core.types import Greeks, Order
from optitrade.greeks.scenario import BookPosition
from optitrade.mcp_server import (
    GreeksSpec,
    LimitsSpec,
    OrderSpec,
    PositionSpec,
    RiskContextSpec,
    VRPConfigSpec,
    build_tools,
)
from optitrade.risk.limits import RiskLimits
from optitrade.strategy.vrp import VRPConfig

pytest.importorskip("mcp", reason="the MCP schema is only generated with the 'mcp' extra")


def _schemas() -> dict[str, dict[str, Any]]:
    """Tool name -> generated JSON schema, via the real server registration.

    The journal directory is a throwaway: ``create_server`` defaults to the
    relative ``runtime_data/``, which would write into the repo the moment this
    helper grew a tool call. It does not today only because ``EventLog`` writes
    lazily and ``list_tools`` never appends.
    """
    import asyncio
    import tempfile

    from optitrade.mcp_server import create_server

    with tempfile.TemporaryDirectory() as tmp:
        server = create_server(journal_dir=Path(tmp), run_id="schema-test")
        tools = asyncio.run(server.list_tools())
    return {t.name: t.input_schema for t in tools}


def _spec_keys(spec: type) -> set[str]:
    return set(get_type_hints(spec, include_extras=False))


def _field_names(cls: type) -> set[str]:
    return {f.name for f in dc.fields(cls)}


# --- Regression guard: no opaque parameters ---------------------------------


def test_no_tool_exposes_a_propertyless_object_parameter() -> None:
    """The original bug: ``{"type": "object"}`` with nothing inside.

    An agent cannot call such a parameter correctly except by luck.
    """
    offenders: list[str] = []
    for tool_name, schema in _schemas().items():
        for param_name, param in schema.get("properties", {}).items():
            # A ``$ref`` points at a named ``$defs`` entry, which is the fix.
            if param.get("type") == "object" and not param.get("properties"):
                offenders.append(f"{tool_name}.{param_name}")
    assert offenders == [], (
        f"these tool parameters serialise to a property-less object, so a calling "
        f"agent cannot discover their fields: {offenders}. Annotate them with a "
        f"TypedDict (see PositionSpec / LimitsSpec) rather than dict[str, Any]."
    )


def test_review_order_schema_names_every_risk_limit() -> None:
    """The specific tool whose opacity mattered most."""
    schema = _schemas()["review_order"]
    limits_props = set(schema["$defs"]["LimitsSpec"]["properties"])
    assert limits_props == _field_names(RiskLimits)

    required = set(schema["$defs"]["LimitsSpec"]["required"])
    # margin_buffer has an engine default, so it is the one optional member.
    assert required == _field_names(RiskLimits) - {"margin_buffer"}


def test_every_tool_parameter_is_described() -> None:
    """Each generated ``$defs`` entry carries its TypedDict docstring."""
    for tool_name, schema in _schemas().items():
        for def_name, definition in schema.get("$defs", {}).items():
            assert definition.get("description"), (
                f"{tool_name}: {def_name} has no description; give its TypedDict "
                f"a docstring stating units and conventions."
            )


# --- Drift guard: specs mirror the engine dataclasses -----------------------


@pytest.mark.parametrize(
    ("spec", "engine_cls"),
    [
        (LimitsSpec, RiskLimits),
        (VRPConfigSpec, VRPConfig),
        (GreeksSpec, Greeks),
        (PositionSpec, BookPosition),
    ],
    ids=["limits", "vrp_config", "greeks", "position"],
)
def test_spec_keys_match_engine_dataclass(spec: type, engine_cls: type) -> None:
    assert _spec_keys(spec) == _field_names(engine_cls), (
        f"{spec.__name__} has drifted from {engine_cls.__name__}. Update the "
        f"TypedDict so the agent-facing schema keeps naming every field."
    )


def test_order_spec_covers_the_fields_review_order_reads() -> None:
    """``Order.contract`` is engine-internal, so OrderSpec is a strict subset."""
    assert _spec_keys(OrderSpec) <= _field_names(Order)
    assert _spec_keys(OrderSpec) == {"symbol", "quantity", "price"}


def test_risk_context_spec_is_fully_optional() -> None:
    """A partial context must review conservatively rather than error.

    ``RiskContextSpec`` is ``total=False`` so every member is optional; the tool
    body supplies 0.0 / empty-Greeks defaults. Marking any member required would
    turn a cautious review into a hard failure.
    """
    schema = _schemas()["review_order"]
    assert schema["$defs"]["RiskContextSpec"].get("required", []) == []
    # Every declared member reaches the schema, so an agent can populate a full
    # context; the body reads exactly these keys via ``.get``.
    assert set(schema["$defs"]["RiskContextSpec"]["properties"]) == _spec_keys(RiskContextSpec)
    assert _spec_keys(RiskContextSpec) == {
        "equity",
        "high_water_mark",
        "margin_available",
        "portfolio_greeks",
        "order_greeks",
        "margin_required",
        "spot",
    }


# --- The bodies still accept plain dicts ------------------------------------


def test_typed_signatures_do_not_change_runtime_behaviour(tmp_path: Any) -> None:
    """TypedDict is erased at runtime: the tools still take plain dicts."""
    from optitrade.journal.event_log import EventLog

    tools = {fn.__name__: fn for fn in build_tools(EventLog(tmp_path, "spec-test"))}
    result = tools["review_order"](
        order={"symbol": "NIFTY24000CE", "quantity": 500.0, "price": 40.0},
        limits={
            "max_abs_delta": 100.0,
            "max_abs_gamma": 5.0,
            "max_abs_vega": 5_000.0,
            "max_drawdown": 0.2,
            "max_concentration": 0.3,
        },
        context={"equity": 1_000_000.0, "spot": 23_873.45},
    )
    assert result["verdict"] in {"approve", "reject", "resize"}
