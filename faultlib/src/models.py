"""Typed domain of the fault library: catalogue entries, taxonomy, rankings."""

from enum import Enum

from pydantic import BaseModel, Field


class ScenarioDomain(str, Enum):
    """The backend a scenario is injected through."""

    CHAOS_MESH = "chaos_mesh"
    SIMULATOR = "simulator"


class TaxonomyCategory(str, Enum):
    """The five failure shapes the library recognises."""

    TRANSIENT = "transient"
    CASCADING = "cascading"
    RESOURCE_EXHAUSTION = "resource-exhaustion"
    NETWORK_PARTITION = "network-partition"
    QUOTA_LIMIT = "quota-limit"


class FaultCatalogEntry(BaseModel):
    """One authored fault library entry."""

    domain: ScenarioDomain
    fault_type: str
    mechanism: str
    description: str
    blast_radius_shape: str
    typical_symptoms: list[str]
    taxonomy_category: TaxonomyCategory
    category_rationale: str
    authored: bool = True


class Classification(BaseModel):
    """The taxonomy verdict for one (domain, fault_type) pair."""

    domain: ScenarioDomain
    fault_type: str
    taxonomy_category: TaxonomyCategory
    rationale: str


class CategoryTally(BaseModel):
    """Per-category counts for one component."""

    transient: int = 0
    cascading: int = 0
    resource_exhaustion: int = 0
    network_partition: int = 0
    quota_limit: int = 0

    def bump(self, category: TaxonomyCategory | str) -> None:
        """Increment the counter for ``category``; unknown values are ignored."""
        name = category.value if isinstance(category, TaxonomyCategory) else str(category)
        name = name.replace("-", "_")
        if name not in type(self).model_fields:
            return
        setattr(self, name, getattr(self, name) + 1)

    @property
    def total(self) -> int:
        """Sum of every category counter."""
        return sum(getattr(self, name) for name in type(self).model_fields)


class ComponentRanking(BaseModel):
    """How often one component was the target of classified scenarios."""

    component: str
    domain: ScenarioDomain
    tally: CategoryTally
    total: int


class RankingsResponse(BaseModel):
    """The full ranking payload, including what was filtered out and why."""

    rankings: list[ComponentRanking] = Field(default_factory=list)
    scenarios_considered: int
    scenarios_excluded: int
    generated_at: str


class CatalogResponse(BaseModel):
    """The catalogue listing envelope."""

    entries: list[FaultCatalogEntry] = Field(default_factory=list)
    total: int
