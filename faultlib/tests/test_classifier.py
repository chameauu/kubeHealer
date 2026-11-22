"""Contract tests for the taxonomy classifier (rows 36-44).

The classifier is a pure lookup over the catalogue: it copies the entry's
category and rationale, and it never guesses for an unknown or mismatched pair.
"""

from classifier import classify
from catalog import all_entries
from models import Classification, ScenarioDomain, TaxonomyCategory

CHAOS_MESH = ScenarioDomain.CHAOS_MESH
SIMULATOR = ScenarioDomain.SIMULATOR


class TestKnownFaultTypes:
    def test_every_catalogued_fault_type_classifies(self) -> None:
        for entry in all_entries():
            where = f"{entry.domain}/{entry.fault_type}"
            classification = classify(entry.domain, entry.fault_type)

            assert classification is not None, f"{where}: no classification produced"
            assert isinstance(classification.taxonomy_category, TaxonomyCategory), (
                f"{where}: category is not a TaxonomyCategory member"
            )
            assert classification.domain == entry.domain, f"{where}: domain not echoed"
            assert classification.fault_type == entry.fault_type, f"{where}: fault_type lost"

    def test_classification_matches_the_catalog_entry_exactly(self) -> None:
        for entry in all_entries():
            where = f"{entry.domain}/{entry.fault_type}"
            classification = classify(entry.domain, entry.fault_type)

            assert classification == Classification(
                domain=entry.domain,
                fault_type=entry.fault_type,
                taxonomy_category=entry.taxonomy_category,
                rationale=entry.category_rationale,
            ), f"{where}: classification diverges from the catalogue entry"

    def test_classification_is_deterministic(self) -> None:
        for entry in all_entries():
            first = classify(entry.domain, entry.fault_type)
            second = classify(entry.domain, entry.fault_type)

            assert first == second, f"{entry.domain}/{entry.fault_type}: unstable classification"


class TestUnknownFaultTypes:
    def test_uncatalogued_fault_type_yields_no_classification(self) -> None:
        assert classify(CHAOS_MESH, "does-not-exist") is None
        assert classify(SIMULATOR, "does-not-exist") is None

    def test_fault_type_from_the_wrong_domain_yields_no_classification(self) -> None:
        assert classify(CHAOS_MESH, "latency") is None
        assert classify(SIMULATOR, "pod_kill") is None

        assert classify(SIMULATOR, "latency") is not None


class TestSpecificMappings:
    def test_quota_limit_maps_to_quota_limit(self) -> None:
        classification = classify(SIMULATOR, "quota_limit")

        assert classification is not None
        assert classification.taxonomy_category == TaxonomyCategory.QUOTA_LIMIT
        assert classification.taxonomy_category.value == "quota-limit"

    def test_network_faults_map_to_network_partition(self) -> None:
        for fault_type in ("network_latency", "packet_loss"):
            classification = classify(CHAOS_MESH, fault_type)

            assert classification is not None, f"{fault_type}: no classification"
            assert classification.taxonomy_category == TaxonomyCategory.NETWORK_PARTITION, (
                f"{fault_type}: not categorised as a network partition"
            )

    def test_pod_kill_maps_to_cascading(self) -> None:
        classification = classify(CHAOS_MESH, "pod_kill")

        assert classification is not None
        assert classification.taxonomy_category == TaxonomyCategory.CASCADING
        assert classification.taxonomy_category.value == "cascading"

    def test_io_delay_maps_to_resource_exhaustion(self) -> None:
        classification = classify(CHAOS_MESH, "io_delay")

        assert classification is not None
        assert classification.taxonomy_category == TaxonomyCategory.RESOURCE_EXHAUSTION
        assert classification.taxonomy_category.value == "resource-exhaustion"
