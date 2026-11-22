import type { Scenario } from '../api/types';
import { isK8sTarget, isSimulatorTarget } from '../lib/scenario';

export function TargetSummary({ target }: { target: Scenario['target'] }) {
  if (isK8sTarget(target)) {
    const labels = Object.entries(target.label_selector ?? {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, value]) => `${key}=${value}`);
    return (
      <span className="target-summary">
        <span className="mono">{target.namespace}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span className="mono">mode {target.mode}</span>
        {target.value ? (
          <>
            <span className="dot-sep" aria-hidden="true">
              ·
            </span>
            <span className="mono">value {target.value}</span>
          </>
        ) : null}
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span className="mono">{labels.length ? labels.join('  ') : 'no labels'}</span>
      </span>
    );
  }

  if (isSimulatorTarget(target)) {
    return (
      <span className="target-summary">
        <span className="mono">resource_type {target.resource_type ?? 'any'}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span className="mono">operation {target.operation ?? 'any'}</span>
      </span>
    );
  }

  return <span className="mono">{JSON.stringify(target)}</span>;
}
