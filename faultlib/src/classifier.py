"""Taxonomy classification is a pure lookup over the catalogue."""

from catalog import lookup
from models import Classification, ScenarioDomain


def classify(domain: ScenarioDomain | str, fault_type: str) -> Classification | None:
    """Return the catalogue's verdict for a pair, or ``None``. Never guesses."""
    entry = lookup(domain, fault_type)
    if entry is None:
        return None
    return Classification(
        domain=entry.domain,
        fault_type=entry.fault_type,
        taxonomy_category=entry.taxonomy_category,
        rationale=entry.category_rationale,
    )
