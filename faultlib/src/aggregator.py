"""Derive component identities from scenario targets and rank them."""

from datetime import datetime, timezone
from typing import Any

from classifier import classify
from models import (
    CategoryTally,
    ComponentRanking,
    RankingsResponse,
    ScenarioDomain,
    TaxonomyCategory,
)

_ANY = "*"
_NO_NAMESPACE = "(no namespace)"
_UNSCOPED = "(unscoped simulator rule)"


def _text(value: Any) -> str:
    return str(value).strip() if value else ""


def _chaos_mesh_component(target: dict[str, Any]) -> str:
    namespace = _text(target.get("namespace"))
    if not namespace:
        return _NO_NAMESPACE
    selector = target.get("label_selector")
    if not isinstance(selector, dict) or not selector:
        return namespace
    labels = ",".join(
        f"{key}={selector[key]}" for key in sorted(selector, key=str)
    )
    return f"{namespace}/{labels}"


def _simulator_component(target: dict[str, Any]) -> str:
    resource_type = _text(target.get("resource_type"))
    operation = _text(target.get("operation"))
    if resource_type and operation:
        return f"{resource_type}/{operation}"
    if resource_type:
        return f"{resource_type}/{_ANY}"
    if operation:
        return f"{_ANY}/{operation}"
    return _UNSCOPED


def derive_component(domain: ScenarioDomain | str, target: dict[str, Any]) -> str:
    """Return the component label for one scenario target."""
    target = target if isinstance(target, dict) else {}
    if not isinstance(domain, ScenarioDomain):
        try:
            domain = ScenarioDomain(domain)
        except (ValueError, TypeError):
            return _chaos_mesh_component(target)
    if domain == ScenarioDomain.SIMULATOR:
        return _simulator_component(target)
    return _chaos_mesh_component(target)


def _parse(
    scenario: Any,
) -> tuple[str, ScenarioDomain, TaxonomyCategory] | None:
    """Return ``(component, domain, category)`` or ``None`` if not countable."""
    if not isinstance(scenario, dict):
        return None
    if scenario.get("started_at") is None:
        return None
    try:
        domain = ScenarioDomain(scenario.get("domain"))
    except (ValueError, TypeError):
        return None
    fault_type = scenario.get("fault_type")
    if not isinstance(fault_type, str) or not fault_type:
        return None
    classification = classify(domain, fault_type)
    if classification is None:
        return None
    component = derive_component(domain, scenario.get("target"))
    return component, domain, classification.taxonomy_category


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_rankings(scenarios: list[dict[str, Any]]) -> RankingsResponse:
    """Aggregate scenario history by component; malformed rows are excluded."""
    groups: dict[tuple[str, ScenarioDomain], ComponentRanking] = {}
    considered = 0
    excluded = 0
    for scenario in scenarios or []:
        parsed = _parse(scenario)
        if parsed is None:
            excluded += 1
            continue
        component, domain, category = parsed
        considered += 1
        key = (component, domain)
        ranking = groups.get(key)
        if ranking is None:
            ranking = ComponentRanking(
                component=component,
                domain=domain,
                tally=CategoryTally(),
                total=0,
            )
            groups[key] = ranking
        ranking.total += 1
        ranking.tally.bump(category)
    rankings = sorted(groups.values(), key=lambda item: (-item.total, item.component))
    return RankingsResponse(
        rankings=rankings,
        scenarios_considered=considered,
        scenarios_excluded=excluded,
        generated_at=_now_iso(),
    )
