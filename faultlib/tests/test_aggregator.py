"""Contract tests for the ranking aggregator (rows 1-15 of the parity gate).

Every assertion here is about derived structure: component identity, exclusion
rules, tally fields and ordering. No assertion depends on catalogue prose.
"""

from typing import Any

from aggregator import build_rankings, derive_component
from fakes import scenario
from models import CategoryTally, ScenarioDomain, TaxonomyCategory

CHAOS_MESH = ScenarioDomain.CHAOS_MESH
SIMULATOR = ScenarioDomain.SIMULATOR

SHOP_TARGET = {"namespace": "shop", "label_selector": {"app": "cart"}}


def _ranking_for(rankings: list[Any], component: str) -> Any:
    matches = [ranking for ranking in rankings if ranking.component == component]
    assert len(matches) == 1, (
        f"expected exactly one ranking for {component!r}, found {len(matches)} in "
        f"{[ranking.component for ranking in rankings]!r}"
    )
    return matches[0]


class TestDeriveComponent:
    def test_chaos_mesh_component_combines_namespace_and_labels(self) -> None:
        target = {"namespace": "shop", "label_selector": {"app": "checkout"}}

        assert derive_component(CHAOS_MESH, target) == "shop/app=checkout"

    def test_chaos_mesh_component_sorts_multiple_labels_deterministically(self) -> None:
        target = {
            "namespace": "shop",
            "label_selector": {"tier": "backend", "app": "checkout", "zone": "eu"},
        }

        assert derive_component(CHAOS_MESH, target) == "shop/app=checkout,tier=backend,zone=eu"

    def test_chaos_mesh_component_falls_back_to_namespace_alone(self) -> None:
        assert derive_component(CHAOS_MESH, {"namespace": "shop", "label_selector": {}}) == "shop"
        assert derive_component(CHAOS_MESH, {"namespace": "shop"}) == "shop"

    def test_chaos_mesh_component_never_invents_a_namespace(self) -> None:
        assert derive_component(CHAOS_MESH, {}) == "(no namespace)"
        assert derive_component(CHAOS_MESH, {"namespace": ""}) == "(no namespace)"

    def test_simulator_component_combines_resource_type_and_operation(self) -> None:
        target = {"resource_type": "volume", "operation": "create"}

        assert derive_component(SIMULATOR, target) == "volume/create"

    def test_simulator_component_handles_partial_targets(self) -> None:
        assert derive_component(SIMULATOR, {"resource_type": "volume"}) == "volume/*"
        assert derive_component(SIMULATOR, {"operation": "delete"}) == "*/delete"
        assert derive_component(SIMULATOR, {}) == "(unscoped simulator rule)"


class TestBuildRankingsEmptyState:
    def test_no_scenarios_yields_no_rankings(self) -> None:
        response = build_rankings([])

        assert response.rankings == []
        assert response.scenarios_considered == 0
        assert response.scenarios_excluded == 0
        assert response.generated_at.endswith("Z")


class TestBuildRankingsExclusions:
    def test_scenarios_that_never_started_are_excluded_not_counted(self) -> None:
        ran = scenario(scenario_id="ran")
        queued = scenario(
            scenario_id="queued",
            started=False,
            target={"namespace": "waiting", "label_selector": {"app": "pending"}},
        )

        response = build_rankings([ran, queued])

        assert response.scenarios_considered == 1
        assert response.scenarios_excluded == 1
        assert [ranking.component for ranking in response.rankings] == ["default/app=web"]
        assert response.rankings[0].total == 1

    def test_unclassifiable_fault_types_are_excluded_not_guessed(self) -> None:
        known = scenario(scenario_id="known")
        unknown = scenario(
            scenario_id="unknown",
            fault_type="not_in_the_library",
            target={"namespace": "ghost", "label_selector": {"app": "ghost"}},
        )

        response = build_rankings([known, unknown])

        assert response.scenarios_considered == 1
        assert response.scenarios_excluded == 1
        assert [ranking.component for ranking in response.rankings] == ["default/app=web"]

    def test_unrecognized_domains_are_excluded(self) -> None:
        known = scenario(scenario_id="known")
        alien = scenario(
            scenario_id="alien",
            domain="quantum_cloud",
            target={"namespace": "elsewhere", "label_selector": {"app": "alien"}},
        )

        response = build_rankings([known, alien])

        assert response.scenarios_considered == 1
        assert response.scenarios_excluded == 1
        assert [ranking.component for ranking in response.rankings] == ["default/app=web"]


class TestBuildRankingsTallying:
    def test_one_real_scenario_produces_exactly_one_tally(self) -> None:
        response = build_rankings([scenario(scenario_id="kill", target=SHOP_TARGET)])

        assert response.scenarios_considered == 1
        assert response.scenarios_excluded == 0
        assert len(response.rankings) == 1
        ranking = response.rankings[0]
        assert ranking.component == "shop/app=cart"
        assert ranking.domain == CHAOS_MESH
        assert ranking.total == 1
        assert ranking.tally.cascading == 1
        assert ranking.tally.transient == 0
        assert ranking.tally.resource_exhaustion == 0
        assert ranking.tally.network_partition == 0
        assert ranking.tally.quota_limit == 0

    def test_same_component_accumulates_across_categories(self) -> None:
        response = build_rankings(
            [
                scenario(scenario_id="kill", fault_type="pod_kill", target=SHOP_TARGET),
                scenario(scenario_id="slow", fault_type="network_latency", target=SHOP_TARGET),
                scenario(scenario_id="loss", fault_type="packet_loss", target=SHOP_TARGET),
            ]
        )

        ranking = _ranking_for(response.rankings, "shop/app=cart")
        assert len(response.rankings) == 1
        assert ranking.total == 3
        assert ranking.tally.cascading == 1
        assert ranking.tally.network_partition == 2
        assert ranking.tally.transient == 0
        assert ranking.tally.resource_exhaustion == 0
        assert ranking.tally.quota_limit == 0

    def test_distinct_components_produce_distinct_rankings(self) -> None:
        response = build_rankings(
            [
                scenario(
                    scenario_id="cart",
                    target={"namespace": "shop", "label_selector": {"app": "cart"}},
                ),
                scenario(
                    scenario_id="pay",
                    target={"namespace": "shop", "label_selector": {"app": "pay"}},
                ),
            ]
        )

        assert response.scenarios_considered == 2
        assert len(response.rankings) == 2
        assert {ranking.component for ranking in response.rankings} == {
            "shop/app=cart",
            "shop/app=pay",
        }

    def test_rankings_are_sorted_by_total_descending(self) -> None:
        quiet = scenario(
            scenario_id="quiet",
            target={"namespace": "shop", "label_selector": {"app": "quiet"}},
        )
        busy = [
            scenario(
                scenario_id="busy-a",
                target={"namespace": "shop", "label_selector": {"app": "busy"}},
            ),
            scenario(
                scenario_id="busy-b",
                target={"namespace": "shop", "label_selector": {"app": "busy"}},
            ),
        ]

        response = build_rankings([quiet, *busy])

        assert [ranking.total for ranking in response.rankings] == [2, 1]
        assert [ranking.component for ranking in response.rankings] == [
            "shop/app=busy",
            "shop/app=quiet",
        ]

    def test_every_tallied_category_is_one_of_the_five_named_in_the_issue(self) -> None:
        expected_fields = {category.value.replace("-", "_") for category in TaxonomyCategory}
        assert set(CategoryTally.model_fields) == expected_fields

        response = build_rankings(
            [
                scenario(
                    scenario_id="kill",
                    fault_type="pod_kill",
                    target={"namespace": "n1", "label_selector": {"app": "a"}},
                ),
                scenario(
                    scenario_id="latency",
                    fault_type="network_latency",
                    target={"namespace": "n2", "label_selector": {"app": "b"}},
                ),
                scenario(
                    scenario_id="loss",
                    fault_type="packet_loss",
                    target={"namespace": "n3", "label_selector": {"app": "c"}},
                ),
                scenario(
                    scenario_id="io",
                    fault_type="io_delay",
                    target={"namespace": "n4", "label_selector": {"app": "d"}},
                ),
                scenario(
                    scenario_id="delay",
                    domain="simulator",
                    fault_type="latency",
                    target={"resource_type": "volume", "operation": "create"},
                ),
                scenario(
                    scenario_id="abort",
                    domain="simulator",
                    fault_type="transient_error",
                    target={"resource_type": "volume", "operation": "delete"},
                ),
                scenario(
                    scenario_id="stall",
                    domain="simulator",
                    fault_type="partial_failure",
                    target={"resource_type": "instance", "operation": "start"},
                ),
                scenario(
                    scenario_id="quota",
                    domain="simulator",
                    fault_type="quota_limit",
                    target={"resource_type": "bucket", "operation": "put"},
                ),
            ]
        )

        totals = dict.fromkeys(expected_fields, 0)
        for ranking in response.rankings:
            for field in expected_fields:
                totals[field] += getattr(ranking.tally, field)

        assert response.scenarios_considered == 8
        assert totals == {
            "transient": 2,
            "cascading": 2,
            "resource_exhaustion": 1,
            "network_partition": 2,
            "quota_limit": 1,
        }
        assert all(value >= 1 for value in totals.values())
