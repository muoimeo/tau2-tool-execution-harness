"""Small, explicit Retail WRITE postcondition allowlist for A3 v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tau2.harness.a1.contracts import canonical_json_sha256
from tau2.harness.a3.contracts import PostconditionResult


@dataclass(frozen=True)
class PostconditionPlan:
    tool_name: str
    order_id: str
    arguments: dict[str, Any]
    rule_id: str
    pre_state_sha256: str


_RULES = {
    "cancel_pending_order": "retail.cancel.status_and_reason/v1",
    "return_delivered_order_items": "retail.return.status_items_payment_method/v1",
    "modify_pending_order_address": "retail.modify_address.fields/v1",
}


def _order(environment, order_id: str):
    return environment.tools.db.orders[order_id]


def _relevant(tool_name: str, order) -> dict[str, Any]:
    if tool_name == "cancel_pending_order":
        return {"status": order.status, "cancel_reason": order.cancel_reason}
    if tool_name == "return_delivered_order_items":
        return {"status": order.status, "return_items": order.return_items, "return_payment_method_id": order.return_payment_method_id}
    if tool_name == "modify_pending_order_address":
        return {name: getattr(order.address, name) for name in ("address1", "address2", "city", "state", "country", "zip")}
    raise KeyError(tool_name)


def capture_before(environment, *, tool_name: str, arguments: dict[str, Any]) -> PostconditionPlan | None:
    if tool_name not in _RULES or not isinstance(arguments.get("order_id"), str):
        return None
    order_id = arguments["order_id"]
    try:
        relevant = _relevant(tool_name, _order(environment, order_id))
    except (AttributeError, KeyError):
        # The native tool owns missing-order/precondition behavior.  A3 has no
        # snapshot for it and must not turn it into a pre-dispatch rejection.
        return None
    return PostconditionPlan(tool_name, order_id, dict(arguments), _RULES[tool_name], canonical_json_sha256(relevant))


def capture_after(environment, plan: PostconditionPlan | None) -> tuple[dict[str, Any] | None, str | None]:
    """Capture selected state immediately after a WRITE returns, before validation."""
    if plan is None:
        return None, None
    observed = _relevant(plan.tool_name, _order(environment, plan.order_id))
    return observed, canonical_json_sha256(observed)


def evaluate(plan: PostconditionPlan | None, observed: dict[str, Any] | None, post_hash: str | None) -> PostconditionResult:
    if plan is None:
        return PostconditionResult(applicable=False)
    if observed is None or post_hash is None:
        raise ValueError("applicable postcondition requires captured post-state")
    args = plan.arguments
    if plan.tool_name == "cancel_pending_order":
        passed = observed == {"status": "cancelled", "cancel_reason": args["reason"]}
    elif plan.tool_name == "return_delivered_order_items":
        passed = observed == {"status": "return requested", "return_items": sorted(args["item_ids"]), "return_payment_method_id": args["payment_method_id"]}
    else:
        passed = observed == {name: args[name] for name in ("address1", "address2", "city", "state", "country", "zip")}
    return PostconditionResult(applicable=True, passed=passed, rule_id=plan.rule_id, pre_state_sha256=plan.pre_state_sha256, post_state_sha256=post_hash)
