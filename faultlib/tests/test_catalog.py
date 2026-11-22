"""Contract tests for the authored fault catalogue (rows 27-35).

The catalogue is data, so the tests pin the partition of fault types, the
completeness of every entry, and the ban on prose that reads like a measurement.
"""

import re
from collections.abc import Iterator

import catalog
from models import FaultCatalogEntry, ScenarioDomain, TaxonomyCategory

CHAOS_MESH = ScenarioDomain.CHAOS_MESH
SIMULATOR = ScenarioDomain.SIMULATOR

MEASUREMENT = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent|ms|seconds?|times|x)\b",
    re.IGNORECASE,
)


def _fault_types(domain: ScenarioDomain) -> set[str]:
    return {entry.fault_type for entry in catalog.for_domain(domain)}


def _prose_fields(entry: FaultCatalogEntry) -> Iterator[str]:
    yield entry.description
    yield entry.blast_radius_shape
    yield entry.category_rationale
    yield from entry.typical_symptoms


class TestCoverage:
    def test_every_chaos_mesh_fault_type_is_catalogued(self) -> None:
        entries = catalog.for_domain(CHAOS_MESH)

        assert len(entries) == 4
        assert {entry.domain for entry in entries} == {CHAOS_MESH}
        assert _fault_types(CHAOS_MESH) == {
            "pod_kill",
            "network_latency",
            "packet_loss",
            "io_delay",
        }

    def test_every_simulator_fault_type_is_catalogued(self) -> None:
        entries = catalog.for_domain(SIMULATOR)

        assert len(entries) == 4
        assert {entry.domain for entry in entries} == {SIMULATOR}
        assert _fault_types(SIMULATOR) == {
            "latency",
            "transient_error",
            "partial_failure",
            "quota_limit",
        }

    def test_no_duplicate_entries(self) -> None:
        entries = catalog.all_entries()
        pairs = [(entry.domain, entry.fault_type) for entry in entries]

        assert len(pairs) == 8
        assert len(set(pairs)) == len(pairs), f"duplicate catalogue keys in {pairs!r}"


class TestEntryCompleteness:
    def test_every_entry_has_populated_metadata(self) -> None:
        for entry in catalog.all_entries():
            where = f"{entry.domain}/{entry.fault_type}"
            assert entry.mechanism.strip(), f"{where}: blank mechanism"
            assert entry.description.strip(), f"{where}: blank description"
            assert entry.blast_radius_shape.strip(), f"{where}: blank blast_radius_shape"
            assert entry.category_rationale.strip(), f"{where}: blank category_rationale"
            assert len(entry.typical_symptoms) >= 2, f"{where}: fewer than two symptoms"
            for symptom in entry.typical_symptoms:
                assert symptom.strip(), f"{where}: blank symptom"

    def test_every_entry_has_a_valid_taxonomy_category(self) -> None:
        valid = set(TaxonomyCategory)

        for entry in catalog.all_entries():
            where = f"{entry.domain}/{entry.fault_type}"
            assert isinstance(entry.taxonomy_category, TaxonomyCategory), (
                f"{where}: category is not a TaxonomyCategory member"
            )
            assert entry.taxonomy_category in valid, f"{where}: unknown category"
            assert entry.taxonomy_category.value in {
                "transient",
                "cascading",
                "resource-exhaustion",
                "network-partition",
                "quota-limit",
            }, f"{where}: category value outside the taxonomy"


class TestNoFabricatedMeasurements:
    def test_no_field_reads_like_a_measured_statistic(self) -> None:
        offenders = [
            (f"{entry.domain}/{entry.fault_type}", text)
            for entry in catalog.all_entries()
            for text in _prose_fields(entry)
            if MEASUREMENT.search(text)
        ]

        assert offenders == [], f"measurement-shaped prose: {offenders!r}"


class TestLookup:
    def test_lookup_returns_the_matching_entry(self) -> None:
        entry = catalog.lookup(CHAOS_MESH, "pod_kill")

        assert entry is not None
        assert entry.domain == CHAOS_MESH
        assert entry.fault_type == "pod_kill"
        assert entry in catalog.all_entries()

    def test_lookup_is_domain_scoped(self) -> None:
        assert catalog.lookup(CHAOS_MESH, "latency") is None

        entry = catalog.lookup(SIMULATOR, "latency")

        assert entry is not None
        assert entry.domain == SIMULATOR
        assert entry.fault_type == "latency"

    def test_lookup_returns_none_for_unknown_fault_type(self) -> None:
        assert catalog.lookup(CHAOS_MESH, "does-not-exist") is None
        assert catalog.lookup(SIMULATOR, "does-not-exist") is None
