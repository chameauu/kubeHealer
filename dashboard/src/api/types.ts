/**
 * Wire types for the KubeHeal console.
 *
 * Sources, in order:
 *   - chaos/openapi.json, graph/openapi.json, faultlib/openapi.json, sim/openapi.json
 *   - agent/openapi.json (envelopes are free-form), so the run document is derived
 *     from agent/src/state.py and agent/src/routers/runs.py as committed.
 *
 * Field names are copied from the schemas; nothing here invents a field the
 * services do not emit.
 */

/* ------------------------------------------------------------------ chaos */

export type ScenarioDomain = 'chaos_mesh' | 'simulator';

/**
 * Full lifecycle union, including `stopping` (chaos/openapi.json,
 * ScenarioStatus). `completed` is deduced only when the backend object is gone.
 */
export type ScenarioStatus =
  | 'pending'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'completed'
  | 'failed';

export type K8sMode = 'one' | 'all' | 'fixed' | 'fixed-percent' | 'random-max-percent';

export interface K8sTarget {
  namespace: string;
  label_selector: Record<string, string>;
  mode: K8sMode;
  value: string | null;
}

export interface SimulatorTarget {
  resource_type?: SimResourceType;
  operation?: string;
}

/** A scenario record. There is no blast radius here: it belongs to an agent run. */
export interface Scenario {
  id: string;
  correlation_id: string | null;
  name: string;
  domain: ScenarioDomain;
  fault_type: string;
  target: K8sTarget | SimulatorTarget | Record<string, unknown>;
  duration_seconds: number | null;
  params: Record<string, unknown>;
  status: ScenarioStatus;
  backend_ref: string | null;
  live_status: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface ScenarioList {
  total: number;
  scenarios: Scenario[];
}

export interface ScenarioCreateRequest {
  name: string;
  correlation_id?: string | null;
  domain: ScenarioDomain;
  fault_type: string;
  target: Record<string, unknown>;
  duration_seconds?: number | null;
  probability?: number;
  params?: Record<string, unknown>;
}

/* ------------------------------------------------------------------ graph */

export type Severity = 'high' | 'medium' | 'low';
export type EdgeType = 'env_ref' | 'flow_observed';

export interface GraphNode {
  id: string;
  name: string;
  namespace: string;
  kind: string;
  cluster_ip: string | null;
  labels: Record<string, string>;
}

export interface GraphEdge {
  source: string;
  target: string;
  edge_type: EdgeType;
  env_var: string | null;
  flow_count: number;
}

export interface TopologyResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  topology_sources: string[];
  node_count: number;
  edge_count: number;
  observed_at?: string;
}

export interface AffectedNode {
  node_id: string;
  name: string;
  namespace: string;
  distance_hops: number;
  severity: Severity;
  via_edge_types?: string[];
}

export interface BlastRadiusResponse {
  target_namespace: string;
  target_selector: Record<string, string>;
  fault_type: string;
  matched_nodes: string[];
  affected_nodes: AffectedNode[];
  topology_sources: string[];
  computed_at?: string;
}

export interface BlastRadiusQuery {
  target_namespace: string;
  fault_type: string;
  selector: Record<string, string>;
}

/* --------------------------------------------------------------- faultlib */

export type TaxonomyCategory =
  | 'transient'
  | 'cascading'
  | 'resource-exhaustion'
  | 'network-partition'
  | 'quota-limit';

export interface FaultCatalogEntry {
  domain: ScenarioDomain;
  fault_type: string;
  mechanism: string;
  description: string;
  blast_radius_shape: string;
  typical_symptoms: string[];
  taxonomy_category: TaxonomyCategory;
  category_rationale: string;
  authored?: boolean;
}

export interface CatalogResponse {
  total: number;
  entries: FaultCatalogEntry[];
}

export interface CategoryTally {
  transient: number;
  cascading: number;
  resource_exhaustion: number;
  network_partition: number;
  quota_limit: number;
}

export interface ComponentRanking {
  component: string;
  domain: ScenarioDomain;
  tally: CategoryTally;
  total: number;
}

export interface RankingsResponse {
  rankings?: ComponentRanking[];
  scenarios_considered: number;
  scenarios_excluded: number;
  generated_at: string;
}

/* ------------------------------------------------------------------ agent */

export type RunStatus = 'running' | 'awaiting_approval' | 'done' | 'aborted' | 'error';
export type ApprovalStatus = 'not_required' | 'pending' | 'approved' | 'rejected';
export type RunOutcome = 'success' | 'failed' | 'aborted';

export interface DiagnosisProvenance {
  mode?: string;
  model?: string | null;
  prompt_sha256?: string;
}

/**
 * The agent stores the provider's reply as `dict[str, Any]`
 * (agent/src/nodes.py). These are the keys the code reads; the index
 * signature keeps an unexpected provider key visible instead of dropping it.
 */
export interface Diagnosis {
  causal_chain?: string[];
  recommended_action?: string;
  action_target?: string;
  risk?: string;
  rationale?: string;
  replicas?: number;
  parse_error?: string | null;
  diagnosis_provenance?: DiagnosisProvenance;
  [key: string]: unknown;
}

/**
 * The run document: `AgentState` (agent/src/state.py) plus the projection
 * fields written by the router and the report node. `node` is the last node
 * that ran; while approval is pending it stays at `heal_plan`.
 */
export interface AgentRun {
  scenario_id: string;
  scenario?: (Partial<Scenario> & Record<string, unknown>) | null;
  status?: RunStatus;
  node?: string;
  approval_status?: ApprovalStatus;
  approval_reason?: string | null;
  action_result?: string | null;
  verify_result?: string | null;
  error?: string | null;
  mttr_seconds?: number | null;
  started_at?: string;
  ended_at?: string | null;
  updated_at?: string;
  fault_type?: string;
  outcome?: RunOutcome;
  diagnosis?: Diagnosis | null;
  /**
   * Evidence gathered at detect time; this is a run field, never a scenario
   * field. A blind source is stored as `{}` by the agent, not as `null`.
   */
  blast_radius?: BlastRadiusResponse | Record<string, unknown> | null;
  catalog_entry?: FaultCatalogEntry | Record<string, unknown> | null;
  memory_context?: string | null;
}

export interface RunList {
  runs: AgentRun[];
  total: number;
}

export interface StartRunRequest {
  scenario_id?: string | null;
  scenario?: Record<string, unknown>;
}

export interface StartRunAccepted {
  scenario_id: string;
  status: string;
}

/** GET /memory/{fault_type} (agent/src/memory.py). */
export interface IncidentMemory {
  incidents: AgentRun[];
  success_rate: number;
  confidence: number;
  mean_mttr_seconds: number | null;
}

/* -------------------------------------------------------------------- sim */

export type SimResourceType = 'volume' | 'subnet' | 'instance';

export interface SimResource {
  id: string;
  name: string;
  type: SimResourceType;
  state: string;
  zone: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SimInstanceList {
  total: number;
  instances: SimResource[];
}

export interface SimSubnetList {
  total: number;
  subnets: SimResource[];
}

export interface SimVolumeList {
  total: number;
  volumes: SimResource[];
}

/** GET /state returns totals plus resources grouped by type (sim/src/store.py). */
export interface SimState {
  totals: Record<SimResourceType, number>;
  resources: Record<SimResourceType, SimResource[]>;
}
