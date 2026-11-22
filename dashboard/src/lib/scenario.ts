import type {
  K8sTarget,
  RunStatus,
  Scenario,
  ScenarioStatus,
  SimulatorTarget,
} from '../api/types';

export const TERMINAL_SCENARIO_STATUSES: readonly ScenarioStatus[] = [
  'stopped',
  'completed',
  'failed',
];

export function isTerminalStatus(status: ScenarioStatus): boolean {
  return TERMINAL_SCENARIO_STATUSES.includes(status);
}

/** Run documents are written by several code paths; derive the status when absent. */
export function runStatus(run: { status?: RunStatus; node?: string }): RunStatus {
  if (run.status) return run.status;
  switch (run.node) {
    case 'report':
      return 'done';
    case 'aborted':
      return 'aborted';
    case 'error':
      return 'error';
    case 'approve':
      return 'awaiting_approval';
    default:
      return 'running';
  }
}

export function isK8sTarget(target: Scenario['target']): target is K8sTarget {
  return (
    typeof target === 'object' &&
    target !== null &&
    typeof (target as { namespace?: unknown }).namespace === 'string'
  );
}

export function isSimulatorTarget(target: Scenario['target']): target is SimulatorTarget {
  return (
    typeof target === 'object' &&
    target !== null &&
    ('resource_type' in target || 'operation' in target) &&
    !('namespace' in target)
  );
}

/** The fault type a run is about: reported on the run, otherwise on its scenario. */
export function runFaultType(run: {
  fault_type?: string;
  scenario?: { fault_type?: string } | null;
}): string | null {
  return run.fault_type ?? run.scenario?.fault_type ?? null;
}

export function runName(run: {
  scenario_id: string;
  scenario?: { name?: string } | null;
}): string {
  return run.scenario?.name?.trim() || run.scenario_id;
}
