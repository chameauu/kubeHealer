import type { AgentRun } from '../api/types';
import { runStatus } from '../lib/scenario';

const STAGES = [
  { id: 'detect', label: 'detect', hint: 'gather' },
  { id: 'diagnose', label: 'diagnose', hint: 'propose' },
  { id: 'heal_plan', label: 'heal_plan', hint: 'plan' },
  { id: 'approve', label: 'approve', hint: 'gate' },
  { id: 'execute', label: 'execute', hint: 'act' },
  { id: 'verify', label: 'verify', hint: 'check' },
  { id: 'report', label: 'report', hint: 'close' },
] as const;

type StageId = (typeof STAGES)[number]['id'];
type StageState = 'done' | 'current' | 'pending' | 'failed' | 'rejected' | 'skipped';

function indexOfStage(id: StageId): number {
  return STAGES.findIndex((stage) => stage.id === id);
}

function activeStage(run: AgentRun): StageId | null {
  const status = runStatus(run);
  if (status === 'awaiting_approval') return 'approve';
  if (status === 'aborted') return 'approve';
  const node = run.node;
  const match = STAGES.find((stage) => stage.id === node);
  return match ? match.id : null;
}

function stateFor(run: AgentRun, index: number): StageState {
  const status = runStatus(run);

  // A low-risk plan executes without the interrupt: the gate was bypassed.
  if (STAGES[index]?.id === 'approve' && run.approval_status === 'not_required') {
    return 'skipped';
  }

  if (status === 'done') return 'done';

  if (status === 'aborted') {
    if (STAGES[index]?.id === 'approve') return 'rejected';
    return index < indexOfStage('approve') ? 'done' : 'skipped';
  }

  const active = activeStage(run);
  const activeIndex = active ? indexOfStage(active) : -1;

  if (status === 'error') {
    if (activeIndex < 0) return 'pending';
    if (index < activeIndex) return 'done';
    if (index === activeIndex) return 'failed';
    return 'pending';
  }

  if (activeIndex < 0) return index === 0 ? 'current' : 'pending';
  if (index < activeIndex) return 'done';
  if (index === activeIndex) return 'current';
  return 'pending';
}

const VISIBLE_STATE: Partial<Record<StageState, string>> = {
  current: 'now',
  failed: 'failed',
  rejected: 'rejected',
  skipped: 'skipped',
};

export function Pipeline({ run }: { run: AgentRun }) {
  const status = runStatus(run);
  const active = activeStage(run);

  return (
    <div className="pipeline-wrap">
      <ol className="pipeline" aria-label="run pipeline">
        {STAGES.map((stage, index) => {
          const state = stateFor(run, index);
          return (
            <li
              className={`pipeline__stage pipeline__stage--${state}${
                active === stage.id ? ' pipeline__stage--active' : ''
              }`}
              key={stage.id}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <span className="pipeline__marker" aria-hidden="true">
                {state === 'done' ? '✓' : index + 1}
              </span>
              <span className="pipeline__text">
                <span className="pipeline__label">{stage.label}</span>
                <span className="pipeline__hint">{stage.hint}</span>
              </span>
              {VISIBLE_STATE[state] ? (
                <span className="pipeline__state">{VISIBLE_STATE[state]}</span>
              ) : null}
              <span className="sr-only">{`${stage.label}: ${state}`}</span>
            </li>
          );
        })}
      </ol>
      <p className="pipeline__footnote">
        <span className="mono">node {run.node ?? 'unknown'}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span className="mono">approval {run.approval_status ?? 'unknown'}</span>
        {status === 'error' && active === null ? (
          <>
            <span className="dot-sep" aria-hidden="true">
              ·
            </span>
            <span className="text-danger">stopped before a pipeline stage was recorded</span>
          </>
        ) : null}
      </p>
    </div>
  );
}
