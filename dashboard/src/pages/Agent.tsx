import { useEffect, useState } from 'react';
import { listRuns } from '../api/agent';
import { EmptyState, LoadingLine } from '../components/EmptyState';
import { ErrorBanner } from '../components/ErrorBanner';
import { Panel } from '../components/Panel';
import { RunDetail } from '../components/RunDetail';
import { RunStatusPill } from '../components/StatusPill';
import { useNow } from '../hooks/useNow';
import { usePoll } from '../hooks/usePoll';
import { formatAge, formatClock, formatTimestamp } from '../lib/format';
import { runFaultType, runName, runStatus } from '../lib/scenario';

const POLL_MS = 2000;

export function Agent({
  focusRunId,
  onFocusConsumed,
}: {
  focusRunId: string | null;
  onFocusConsumed: () => void;
}) {
  const now = useNow(1000);
  const runsPoll = usePoll(() => listRuns(), POLL_MS);
  const runs = runsPoll.data?.runs ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (focusRunId) {
      setSelectedId(focusRunId);
      onFocusConsumed();
    }
  }, [focusRunId, onFocusConsumed]);

  useEffect(() => {
    if (!selectedId && runs.length > 0) {
      const first = runs[0];
      if (first) setSelectedId(first.scenario_id);
    }
  }, [runs, selectedId]);

  const waiting = runs.filter((run) => runStatus(run) === 'awaiting_approval').length;

  return (
    <div className="page page--split">
      <aside className="pane pane--list">
        <div className="pane__head">
          <div>
            <h1 className="pane__title">agent runs</h1>
            <p className="pane__meta">
              <span className="mono">{runsPoll.data?.total ?? runs.length}</span> runs
              {waiting > 0 ? (
                <>
                  <span className="dot-sep" aria-hidden="true">
                    ·
                  </span>
                  <span className="text-warn">{waiting} waiting for approval</span>
                </>
              ) : null}
              <span className="dot-sep" aria-hidden="true">
                ·
              </span>
              synced{' '}
              <span className="mono">
                {runsPoll.lastUpdatedAt
                  ? formatClock(runsPoll.lastUpdatedAt)
                  : runsPoll.loading
                    ? 'connecting'
                    : '—'}
              </span>
            </p>
          </div>
          <button
            type="button"
            className="btn btn--ghost btn--xs"
            onClick={runsPoll.refresh}
            aria-label="refresh runs"
          >
            refresh
          </button>
        </div>

        {runsPoll.error ? (
          <ErrorBanner
            error={runsPoll.error}
            title="run list refresh failed"
            note={
              runsPoll.data
                ? 'showing the last successful read.'
                : 'the list is empty because the agent did not answer.'
            }
            onRetry={runsPoll.refresh}
          />
        ) : null}

        <div className="pane__scroll">
          {runsPoll.loading && !runsPoll.data ? <LoadingLine label="reading runs" /> : null}

          {!runsPoll.loading && runsPoll.data && runs.length === 0 ? (
            <EmptyState title="no agent runs yet">
              Start a diagnosis from a scenario on Incidents. The agent will gather evidence,
              propose an action and park here when a human is needed.
            </EmptyState>
          ) : null}

          <ul className="scenario-list">
            {runs.map((run) => {
              const status = runStatus(run);
              const isSelected = run.scenario_id === selectedId;
              const faultType = runFaultType(run);
              return (
                <li
                  key={run.scenario_id}
                  className={`scenario-item run-item${isSelected ? ' is-selected' : ''}`}
                  onClick={() => setSelectedId(run.scenario_id)}
                >
                  <div className="scenario-item__head">
                    <button
                      type="button"
                      className="scenario-item__pick"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedId(run.scenario_id);
                      }}
                      aria-pressed={isSelected}
                    >
                      <span className="scenario-item__name">{runName(run)}</span>
                    </button>
                    <RunStatusPill status={status} />
                  </div>
                  <div className="scenario-item__meta">
                    <span className="mono">{faultType ?? 'fault unknown'}</span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span className="mono">node {run.node ?? '—'}</span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span title={formatTimestamp(run.updated_at ?? run.started_at)}>
                      {formatAge(run.updated_at ?? run.started_at, now)} ago
                    </span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span className="scenario-item__id mono">{run.scenario_id}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </aside>

      <section className="pane pane--detail">
        <div className="pane__scroll">
          <div className="detail-stack">
            {selectedId ? (
              <RunDetail
                key={selectedId}
                runId={selectedId}
                onChanged={runsPoll.refresh}
              />
            ) : (
              <Panel title="run">
                <EmptyState title="no run selected">
                  {runs.length > 0
                    ? 'Pick a run on the left to read its pipeline, diagnosis and evidence.'
                    : 'Runs appear here once a scenario is handed to the agent from Incidents.'}
                </EmptyState>
              </Panel>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
