import { useCallback, useEffect, useState } from 'react';
import { getMemory } from '../api/agent';
import { toApiError, type ApiError } from '../api/client';
import type { IncidentMemory, RunOutcome } from '../api/types';
import { formatPercent, formatRate, formatSeconds, formatTimestamp, plural } from '../lib/format';
import { EmptyState, LoadingLine } from './EmptyState';
import { ErrorBanner } from './ErrorBanner';
import { Pill } from './StatusPill';

const OUTCOME_TONES: Record<RunOutcome, 'ok' | 'danger' | 'muted'> = {
  success: 'ok',
  failed: 'danger',
  aborted: 'muted',
};

function outcomeOf(incident: IncidentMemory['incidents'][number]): RunOutcome {
  if (incident.outcome) return incident.outcome;
  if (incident.status === 'done') return 'success';
  if (incident.status === 'aborted') return 'aborted';
  return 'failed';
}

export function MemoryPanel({
  faultType,
  refreshKey = 0,
}: {
  faultType: string;
  refreshKey?: number;
}) {
  const [memory, setMemory] = useState<IncidentMemory | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getMemory(faultType)
      .then((value) => {
        if (!alive) return;
        setMemory(value);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (alive) setError(toApiError(caught));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [faultType, refreshKey, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return (
    <section className="panel memory-panel">
      <header className="panel__head">
        <h2 className="panel__title">
          memory <span className="panel__subject mono">{faultType}</span>
        </h2>
        <div className="panel__actions">
          <button type="button" className="btn btn--ghost btn--xs" onClick={reload}>
            reload
          </button>
        </div>
      </header>
      <div className="panel__body">
        {error ? (
          <ErrorBanner
            error={error}
            title="could not read incident memory"
            onRetry={reload}
            note="figures below are the last successful read."
          />
        ) : null}

        {loading && !memory ? <LoadingLine label="reading memory" /> : null}

        {memory ? (
          <>
            <div className="stats">
              <div className="stat">
                <span className="stat__label">success rate</span>
                <span className="stat__value mono">
                  {memory.incidents.length === 0 ? '—' : formatRate(memory.success_rate)}
                </span>
                <span className="stat__note">
                  {memory.incidents.length === 0 ? 'no incidents yet' : 'aborted runs excluded'}
                </span>
              </div>
              <div className="stat">
                <span className="stat__label">confidence</span>
                <span className="stat__value mono">
                  {memory.incidents.length === 0 ? '—' : formatPercent(memory.confidence)}
                </span>
                <span className="stat__note">
                  {memory.incidents.length === 0
                    ? 'no incidents yet'
                    : 'share of judged runs that succeeded'}
                </span>
              </div>
              <div className="stat">
                <span className="stat__label">mean mttr</span>
                <span className="stat__value mono">
                  {memory.incidents.length === 0
                    ? '—'
                    : formatSeconds(memory.mean_mttr_seconds)}
                </span>
                <span className="stat__note">
                  {memory.incidents.length === 0 ? 'no incidents yet' : 'successful runs only'}
                </span>
              </div>
            </div>

            {memory.incidents.length === 0 ? (
              <EmptyState title="no incidents recorded for this fault type">
                Incident memory fills when a run reaches report; nothing has reported{' '}
                <span className="mono">{faultType}</span> yet.
              </EmptyState>
            ) : (
              <>
                <div className="table-scroll">
                  <table className="table table--compact">
                    <thead>
                      <tr>
                        <th scope="col">ended</th>
                        <th scope="col">outcome</th>
                        <th scope="col">action</th>
                        <th scope="col" className="num">
                          mttr
                        </th>
                        <th scope="col">approval</th>
                      </tr>
                    </thead>
                    <tbody>
                      {memory.incidents.map((incident, index) => {
                        const outcome = outcomeOf(incident);
                        return (
                          <tr key={`${incident.scenario_id}-${index}`}>
                            <td className="mono nowrap" title={incident.ended_at ?? undefined}>
                              {formatTimestamp(incident.ended_at)}
                            </td>
                            <td>
                              <Pill tone={OUTCOME_TONES[outcome]}>{outcome}</Pill>
                            </td>
                            <td
                              className="truncate"
                              title={incident.action_result ?? undefined}
                            >
                              {incident.action_result ?? 'not recorded'}
                            </td>
                            <td className="mono num">
                              {formatSeconds(incident.mttr_seconds)}
                            </td>
                            <td className="mono">
                              {incident.approval_status ?? '—'}
                              {incident.approval_reason ? (
                                <span className="muted"> · {incident.approval_reason}</span>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="table-note">
                  {plural(memory.incidents.length, 'incident')} — the most recent for this fault
                  type, newest first. Values are read from the payload, not recomputed here.
                </p>
              </>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
