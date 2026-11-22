import type { ReactNode } from 'react';
import type { RunStatus, ScenarioStatus } from '../api/types';

export type Tone = 'accent' | 'neutral' | 'warn' | 'ok' | 'muted' | 'danger';

export function Pill({
  tone,
  children,
  pulse = false,
  title,
}: {
  tone: Tone;
  children: ReactNode;
  pulse?: boolean;
  title?: string;
}) {
  return (
    <span className={`pill pill--${tone}${pulse ? ' pill--pulse' : ''}`} title={title}>
      <span className="pill__dot" aria-hidden="true" />
      {children}
    </span>
  );
}

const SCENARIO_TONES: Record<ScenarioStatus, Tone> = {
  pending: 'neutral',
  running: 'accent',
  stopping: 'warn',
  stopped: 'muted',
  completed: 'ok',
  failed: 'danger',
};

export function ScenarioStatusPill({ status }: { status: ScenarioStatus }) {
  return (
    <Pill tone={SCENARIO_TONES[status]} pulse={status === 'stopping'}>
      {status}
    </Pill>
  );
}

const RUN_TONES: Record<RunStatus, Tone> = {
  running: 'accent',
  awaiting_approval: 'warn',
  done: 'ok',
  aborted: 'muted',
  error: 'danger',
};

export function RunStatusPill({ status }: { status: RunStatus }) {
  return (
    <Pill tone={RUN_TONES[status]} pulse={status === 'awaiting_approval'}>
      {status.replace('_', ' ')}
    </Pill>
  );
}

const SEVERITY_TONES: Record<string, Tone> = {
  high: 'danger',
  medium: 'warn',
  low: 'neutral',
};

/** Colour and word together: severity never relies on hue alone. */
export function SeverityBadge({ severity }: { severity: string }) {
  const tone = SEVERITY_TONES[severity.toLowerCase()] ?? 'muted';
  return <Pill tone={tone}>{severity.toLowerCase()}</Pill>;
}
