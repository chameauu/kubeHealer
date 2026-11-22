"""The authored fault catalogue: four chaos-mesh and four simulator entries.

Prose describes observable behaviour only; no entry quotes a measured statistic.
"""

from models import FaultCatalogEntry, ScenarioDomain, TaxonomyCategory

CHAOS_MESH = ScenarioDomain.CHAOS_MESH
SIMULATOR = ScenarioDomain.SIMULATOR

CATALOG: list[FaultCatalogEntry] = [
    FaultCatalogEntry(
        domain=CHAOS_MESH,
        fault_type="pod_kill",
        mechanism="`PodChaos{action: pod-kill}` deletes the selected pod(s); "
        "the ReplicaSet recreates them.",
        description=(
            "One pod disappears without warning. In-flight requests on that pod fail "
            "immediately, the ReplicaSet schedules a replacement, and callers see a short "
            "burst of connection errors followed by recovery once the new pod is ready and "
            "endpoints are repopulated."
        ),
        blast_radius_shape="single_pod",
        typical_symptoms=[
            "connection refused or reset from a single endpoint",
            "HTTP 502/503 spikes while the replacement starts",
            "pod restart count increment",
            "endpoints list briefly missing one address",
        ],
        taxonomy_category=TaxonomyCategory.CASCADING,
        category_rationale=(
            "the failure is local but propagates to every caller for the duration of the "
            "restart, which is the defining property of a cascade rather than an isolated "
            "fault."
        ),
    ),
    FaultCatalogEntry(
        domain=CHAOS_MESH,
        fault_type="network_latency",
        mechanism="`NetworkChaos{action: delay}` adds a fixed latency and jitter between "
        "the target and its peers.",
        description=(
            "Traffic still flows, but every request to the target takes the added delay. "
            "Timeouts at the caller turn into retries, and the retries add load to a "
            "dependency that is already slow."
        ),
        blast_radius_shape="service_pair",
        typical_symptoms=[
            "p99 latency rising by roughly the injected delay",
            "deadline-exceeded errors at callers",
            "retry counts and queue depth climbing",
            "no drop in pod readiness",
        ],
        taxonomy_category=TaxonomyCategory.NETWORK_PARTITION,
        category_rationale=(
            "the link is degraded rather than severed, so the observable damage is loss of "
            "connectivity quality between two peers."
        ),
    ),
    FaultCatalogEntry(
        domain=CHAOS_MESH,
        fault_type="packet_loss",
        mechanism="`NetworkChaos{action: loss}` drops a percentage of packets on the "
        "target's traffic.",
        description=(
            "A share of packets never arrives. TCP retransmits hide the loss for small "
            "requests and punish large ones, so throughput collapses while latency climbs "
            "unevenly."
        ),
        blast_radius_shape="service_pair",
        typical_symptoms=[
            "intermittent timeouts with no single failing endpoint",
            "retransmission counters rising",
            "connections that succeed on retry",
            "throughput far below the request rate",
        ],
        taxonomy_category=TaxonomyCategory.NETWORK_PARTITION,
        category_rationale=(
            "same link-level loss of connectivity as latency, with a different signature: "
            "loss rather than delay."
        ),
    ),
    FaultCatalogEntry(
        domain=CHAOS_MESH,
        fault_type="io_delay",
        mechanism="`IOChaos{action: latency}` delays file operations on the target's "
        "volume path.",
        description=(
            "Disk reads and writes take longer than the application's budget. Threads and "
            "connection pool slots are held while waiting on I/O, so the service runs out "
            "of concurrency before it runs out of CPU."
        ),
        blast_radius_shape="single_pod",
        typical_symptoms=[
            "request latency growing with payload size",
            "high iowait with low CPU",
            "connection pool saturation",
            "write timeouts in the application log",
        ],
        taxonomy_category=TaxonomyCategory.RESOURCE_EXHAUSTION,
        category_rationale=(
            "the visible failure is exhaustion of a bounded resource — worker slots held by "
            "slow I/O — not a network symptom."
        ),
    ),
    FaultCatalogEntry(
        domain=SIMULATOR,
        fault_type="latency",
        mechanism="the simulator's `latency` rule appends `extra_seconds` to one "
        "provisioning operation.",
        description=(
            "A single operation takes longer than usual and then completes normally. "
            "Nothing fails, no state is lost, and the only evidence is a longer "
            "transitional state."
        ),
        blast_radius_shape="single_resource",
        typical_symptoms=[
            "one operation spending noticeably longer in `creating`/`provisioning`",
            "no error status",
            "subsequent operations unaffected",
        ],
        taxonomy_category=TaxonomyCategory.TRANSIENT,
        category_rationale="self-clearing slowdown of one operation, with no persistent damage.",
    ),
    FaultCatalogEntry(
        domain=SIMULATOR,
        fault_type="transient_error",
        mechanism="the simulator's `transient_error` rule drives the resource to `error` "
        "at half the normal delay.",
        description=(
            "The operation fails part way through and the resource lands in `error`. The "
            "failure is repeatable by re-issuing the request, and no partial object is left "
            "attached to anything."
        ),
        blast_radius_shape="single_resource",
        typical_symptoms=[
            "resource state `error` after a create or attach",
            "a 409 on retry against the failed state",
            "upstream error text naming the injection",
        ],
        taxonomy_category=TaxonomyCategory.TRANSIENT,
        category_rationale=(
            "the fault interrupts one operation and leaves a recoverable state; it does not "
            "spread."
        ),
    ),
    FaultCatalogEntry(
        domain=SIMULATOR,
        fault_type="partial_failure",
        mechanism="the simulator's `partial_failure` rule stops the transition at "
        "`degraded` and never reaches a terminal state.",
        description=(
            "The resource becomes usable but incomplete, and stays that way. Anything that "
            "depends on it inherits the degradation: reads may succeed, writes may not, and "
            "the resource never becomes healthy on its own."
        ),
        blast_radius_shape="namespace_wide",
        typical_symptoms=[
            "a resource stuck in `degraded`",
            "dependants reporting partial results",
            "retries that never succeed",
            "no `completed` transition",
        ],
        taxonomy_category=TaxonomyCategory.CASCADING,
        category_rationale=(
            "the stoppage is permanent, so every dependent keeps failing — the cascade is "
            "the point, not a side effect."
        ),
    ),
    FaultCatalogEntry(
        domain=SIMULATOR,
        fault_type="quota_limit",
        mechanism="a `quota_limit` rule (or the baseline per-type quota) refuses a create "
        "before any resource is written.",
        description=(
            "The provider says no. The request is rejected outright with the current usage "
            "against the limit, and nothing is persisted, so the failure is deterministic "
            "and cheap to observe."
        ),
        blast_radius_shape="namespace_wide",
        typical_symptoms=[
            "immediate 409 on create with a quota message",
            "resource count unchanged",
            "no partial object",
            "later creates still refused until something is deleted",
        ],
        taxonomy_category=TaxonomyCategory.QUOTA_LIMIT,
        category_rationale=(
            "this is a policy refusal rather than an infrastructure fault — it is "
            "categorised separately because the remedy is deletion, not retry or restart."
        ),
    ),
]


def _resolve(domain: ScenarioDomain | str) -> ScenarioDomain | None:
    if isinstance(domain, ScenarioDomain):
        return domain
    try:
        return ScenarioDomain(domain)
    except (ValueError, TypeError):
        return None


def all_entries() -> list[FaultCatalogEntry]:
    """Every catalogued entry, chaos-mesh first."""
    return list(CATALOG)


def for_domain(domain: ScenarioDomain | str) -> list[FaultCatalogEntry]:
    """Entries belonging to ``domain``; empty for an unknown domain."""
    resolved = _resolve(domain)
    if resolved is None:
        return []
    return [entry for entry in CATALOG if entry.domain == resolved]


def lookup(
    domain: ScenarioDomain | str, fault_type: str
) -> FaultCatalogEntry | None:
    """The entry for ``(domain, fault_type)``, or ``None``. Never raises."""
    for entry in for_domain(domain):
        if entry.fault_type == fault_type:
            return entry
    return None
