import { useState, type ReactNode } from 'react';
import { approveRun, getRun, rejectRun } from '../api/agent';
import { toApiError, type ApiError } from '../api/client';
import type {
  AgentRun,
  BlastRadiusResponse,
  FaultCatalogEntry,
} from '../api/types';
import { usePoll } from '../hooks/usePoll';
import {
  formatSeconds,
  formatTimestamp,
  readNumber,
  shortHash,
} from '../lib/format';
import { runFaultType, runName, runStatus } from '../lib/scenario';
import { EmptyState, LoadingLine } from '../components/EmptyState';
import { ErrorBanner } from '../components/ErrorBanner';
import { Fields, Panel } from '../components/Panel';
import { Pipeline } from '../components/Pipeline';
import { MemoryPanel } from '../components/MemoryPanel';
import { RunStatusPill, SeverityBadge } from '../components/StatusPill';
import { TargetSummary } from '../components/TargetSummary';

function asBlastRadius(value: AgentRun['blast_radius']): BlastRadiusResponse | null {
  if (!value || typeof value !== 'object') return null;
  if (!('affected_nodes' in value) || !Array.isArray(value.affected_nodes)) return null;
  return value as BlastRadiusResponse;
}

function asCatalogEntry(value: AgentRun['catalog_entry']): FaultCatalogEntry | null {
  if (!value || typeof value !== 'object') return null;
  if (!('fault_type' in value) || typeof value.fault_type !== 'string') return null;
  return value as FaultCatalogEntry;
}

function stringField(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

export function RunDetail({ runId, onChanged }: { runId: string; onChanged: () => void }) {
  const detail = usePoll(() => getRun(runId), 2000);
  const run = detail.data;

  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const [decisionError, setDecisionError] = useState<ApiError | null>(null);
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null);
  const [memoryNonce, setMemoryNonce] = useState(0);

  async function decide(kind: 'approve' | 'reject') {
    setBusy(kind);
    setDecisionError(null);
    setDecisionNotice(null);
    try {
      const updated = await (kind === 'approve' ? approveRun : rejectRun)(runId, reason);
      setReason('');
      setDecisionNotice(
        kind === 'approve'
          ? `approved — ${updated.scenario_id} is ${updated.status ?? 'resuming'}`
          : `rejected — ${updated.scenario_id} is ${updated.status ?? 'aborting'}`,
      );
      detail.refresh();
      onChanged();
      setMemoryNonce((value) => value + 1);
    } catch (caught) {
      const error = toApiError(caught);
      setDecisionError(error);
      detail.refresh();
      onChanged();
    } finally {
      setBusy(null);
    }
  }

  if (!run) {
    return (
      <Panel title="run">
        {detail.error ? (
          <ErrorBanner
            error={detail.error}
            title={detail.error.status === 404 ? 'run not found yet' : 'could not read this run'}
            note={
              detail.error.status === 404
                ? 'the agent may not have written the projection yet; this panel keeps polling.'
                : 'the console keeps the last read when a refresh fails.'
            }
            onRetry={detail.refresh}
          />
        ) : (
          <LoadingLine label="reading run" />
        )}
      </Panel>
    );
  }

  const status = runStatus(run);
  const pending = status === 'awaiting_approval' || run.approval_status === 'pending';
  const diagnosis = run.diagnosis ?? null;
  const chain =
    diagnosis && Array.isArray(diagnosis.causal_chain)
      ? diagnosis.causal_chain.filter((step): step is string => typeof step === 'string')
      : [];
  const provenance = diagnosis?.diagnosis_provenance;
  const confidence = readNumber(diagnosis?.['confidence']);
  const blast = asBlastRadius(run.blast_radius);
  const catalog = asCatalogEntry(run.catalog_entry);
  const faultType = runFaultType(run);
  const scenario = run.scenario;
  const parseError = stringField(diagnosis?.parse_error);
  const memoryContext = stringField(run.memory_context);

  const decisionFields: Array<{ label: string; value: ReactNode; mono?: boolean; wide?: boolean }> = [];
  if (run.approval_status) {
    decisionFields.push({
      label: 'approval',
      value: `${run.approval_status}${run.approval_reason ? ` · ${run.approval_reason}` : ''}`,
      mono: true,
    });
  }
  if (run.action_result) {
    decisionFields.push({ label: 'action result', value: run.action_result, wide: true });
  }
  if (run.verify_result) {
    decisionFields.push({ label: 'verification', value: run.verify_result, mono: true });
  }
  if (run.outcome) {
    decisionFields.push({ label: 'outcome', value: run.outcome, mono: true });
  }
  decisionFields.push({
    label: 'mttr',
    value:
      run.mttr_seconds === null || run.mttr_seconds === undefined
        ? 'not recorded'
        : formatSeconds(run.mttr_seconds),
    mono: true,
  });

  const decided =
    run.approval_status === 'pending' ||
    run.approval_status === 'approved' ||
    run.approval_status === 'rejected';

  const showDecision =
    pending ||
    Boolean(decisionError) ||
    Boolean(decisionNotice) ||
    decided ||
    status === 'done' ||
    status === 'aborted' ||
    status === 'error';

  return (
    <>
      <Panel
        title={<span className="run-title">{runName(run)}</span>}
        meta={<RunStatusPill status={status} />}
        actions={
          <button type="button" className="btn btn--ghost btn--xs" onClick={detail.refresh}>
            refresh
          </button>
        }
      >
        {detail.error ? (
          <ErrorBanner
            error={detail.error}
            title="run refresh failed"
            note="showing the last successful read."
            onRetry={detail.refresh}
          />
        ) : null}

        <Fields
          rows={[
            { label: 'run id', value: run.scenario_id, mono: true },
            { label: 'fault type', value: faultType ?? 'not recorded', mono: true },
            { label: 'started', value: formatTimestamp(run.started_at), mono: true },
            { label: 'updated', value: formatTimestamp(run.updated_at), mono: true },
            { label: 'ended', value: formatTimestamp(run.ended_at), mono: true },
            {
              label: 'mttr',
              value:
                run.mttr_seconds === null || run.mttr_seconds === undefined
                  ? 'not recorded'
                  : formatSeconds(run.mttr_seconds),
              mono: true,
            },
          ]}
        />

        {run.error ? (
          <div className="inline-error">
            <span className="inline-error__label">run error</span>
            <p>{run.error}</p>
          </div>
        ) : null}

        <details className="raw-block">
          <summary>raw run document</summary>
          <pre className="code-block">{JSON.stringify(run, null, 2)}</pre>
        </details>
      </Panel>

      <Panel
        title="pipeline"
        meta={<span className="mono">polled every 2s</span>}
      >
        <Pipeline run={run} />
      </Panel>

      <Panel title="diagnosis" meta={diagnosis ? <span className="mono">proposed by the agent</span> : null}>
        {!diagnosis ? (
          <EmptyState title="no diagnosis recorded yet">
            The run has not reached diagnose. It is at{' '}
            <span className="mono">{run.node ?? 'an unknown node'}</span>.
          </EmptyState>
        ) : (
          <>
            <div className="diagnosis-head">
              <div>
                <span className="field__label">proposed action</span>
                <p className="diagnosis-action mono">
                  {diagnosis.recommended_action ?? 'not proposed'}
                </p>
                <p className="diagnosis-target mono">
                  target {diagnosis.action_target ?? 'not proposed'}
                </p>
              </div>
              <div className="diagnosis-risk">
                <span className="field__label">risk</span>
                <SeverityBadge
                  severity={typeof diagnosis.risk === 'string' ? diagnosis.risk : 'unknown'}
                />
              </div>
            </div>

            <Fields
              rows={[
                {
                  label: 'confidence',
                  value:
                    confidence === null ? (
                      <span className="muted">not reported by the agent</span>
                    ) : (
                      <span className="mono">{confidence}</span>
                    ),
                  wide: true,
                },
                {
                  label: 'provenance',
                  value: (
                    <span className="mono">
                      {provenance?.mode ?? 'mode not recorded'}
                      <span className="dot-sep" aria-hidden="true">
                        ·
                      </span>
                      {provenance?.model ?? 'no model'}
                      <span className="dot-sep" aria-hidden="true">
                        ·
                      </span>
                      sha {shortHash(provenance?.prompt_sha256)}
                    </span>
                  ),
                  wide: true,
                },
              ]}
            />

            <div className="causal">
              <span className="field__label">causal chain</span>
              {chain.length === 0 ? (
                <p className="muted">the agent reported no causal chain.</p>
              ) : (
                <ol className="causal__list">
                  {chain.map((step, index) => (
                    <li key={`${index}-${step}`}>{step}</li>
                  ))}
                </ol>
              )}
            </div>

            {diagnosis.rationale ? (
              <div className="rationale">
                <span className="field__label">rationale</span>
                <p>{diagnosis.rationale}</p>
              </div>
            ) : null}

            {parseError ? (
              <div className="inline-error">
                <span className="inline-error__label">provider output was unusable</span>
                <p>{parseError}</p>
                <p className="inline-error__note">
                  The agent failed closed: it proposed stopping the scenario rather than acting on
                  an unparseable diagnosis.
                </p>
              </div>
            ) : null}

            <details className="raw-block">
              <summary>raw diagnosis payload</summary>
              <pre className="code-block">{JSON.stringify(diagnosis, null, 2)}</pre>
            </details>
          </>
        )}
      </Panel>

      <Panel title="evidence" meta={<span className="mono">gathered at detect</span>}>
        <div className="evidence-grid">
          <section className="evidence-block">
            <h3>scenario</h3>
            {scenario ? (
              <Fields
                rows={[
                  { label: 'name', value: scenario.name ?? 'not recorded' },
                  { label: 'id', value: scenario.id ?? run.scenario_id, mono: true },
                  { label: 'fault', value: scenario.fault_type ?? 'not recorded', mono: true },
                  { label: 'domain', value: scenario.domain ?? 'not recorded', mono: true },
                  ...(scenario.target
                    ? [
                        {
                          label: 'target',
                          value: <TargetSummary target={scenario.target} />,
                          wide: true,
                        },
                      ]
                    : []),
                ]}
              />
            ) : (
              <p className="muted">no scenario snapshot was stored on this run.</p>
            )}
          </section>

          <section className="evidence-block">
            <h3>blast radius</h3>
            {!blast ? (
              <p className="muted">
                no blast radius recorded — detect found a blind or empty source.
              </p>
            ) : (
              <>
                <p className="evidence-line">
                  <span className="mono">
                    {blast.target_namespace
                      ? `${blast.target_namespace} / ${blast.fault_type}`
                      : `no target namespace · ${blast.fault_type}`}
                  </span>
                  <span className="dot-sep" aria-hidden="true">
                    ·
                  </span>
                  {blast.affected_nodes.length} affected · {blast.matched_nodes.length} matched
                </p>
                <div className="tag-row">
                  {(blast.topology_sources ?? []).map((source) => (
                    <span
                      key={source}
                      className={`tag${source === 'synthetic_fixture' ? ' tag--synthetic' : ''}`}
                    >
                      {source}
                    </span>
                  ))}
                </div>
                {blast.topology_sources?.includes('synthetic_fixture') ? (
                  <p className="synthetic-note">synthetic fixture — not a measured cluster.</p>
                ) : null}
                {blast.affected_nodes.length === 0 ? (
                  <p className="muted">graph answered with no affected nodes.</p>
                ) : (
                  <div className="table-scroll table-scroll--short">
                    <table className="table table--compact">
                      <thead>
                        <tr>
                          <th scope="col">node</th>
                          <th scope="col" className="num">
                            hops
                          </th>
                          <th scope="col">severity</th>
                        </tr>
                      </thead>
                      <tbody>
                        {blast.affected_nodes.map((node) => (
                          <tr key={node.node_id}>
                            <td className="mono truncate" title={node.node_id}>
                              {node.name}
                            </td>
                            <td className="mono num">{node.distance_hops}</td>
                            <td>
                              <SeverityBadge severity={node.severity} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="evidence-block evidence-block--wide">
            <h3>catalogue entry</h3>
            {!catalog ? (
              <p className="muted">
                no catalogue entry recorded — the fault may be uncatalogued, or faultlib did not
                answer.
              </p>
            ) : (
              <>
                <div className="tag-row">
                  <span className="tag tag--mono">{catalog.fault_type}</span>
                  <span className="tag">{catalog.taxonomy_category}</span>
                  {catalog.authored ? <span className="tag">authored</span> : null}
                </div>
                <p className="evidence-copy">{catalog.description}</p>
                <Fields
                  rows={[
                    { label: 'mechanism', value: catalog.mechanism, wide: true },
                    { label: 'blast shape', value: catalog.blast_radius_shape, mono: true },
                  ]}
                />
                {catalog.typical_symptoms?.length ? (
                  <ul className="symptom-list">
                    {catalog.typical_symptoms.map((symptom) => (
                      <li key={symptom}>{symptom}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
          </section>

          <section className="evidence-block evidence-block--wide">
            <h3>memory context</h3>
            {memoryContext ? (
              <pre className="code-block code-block--wrap">{memoryContext}</pre>
            ) : (
              <p className="muted">none provided — no comparable incident was passed to this run.</p>
            )}
          </section>
        </div>
      </Panel>

      {showDecision ? (
        <Panel
          title={pending ? 'approval gate' : 'decision'}
          meta={
            pending ? (
              <span className="mono text-warn">waiting for a human</span>
            ) : (
              <span className="mono">settled</span>
            )
          }
        >
          {decisionError ? (
            <ErrorBanner
              error={decisionError}
              title="approval was not applied"
              note={
                decisionError.status === 409
                  ? 'this run is no longer waiting for approval; the list and this run were refreshed.'
                  : undefined
              }
            />
          ) : null}

          {decisionNotice ? (
            <div className="notice" role="status">
              {decisionNotice}
            </div>
          ) : null}

          {pending ? (
            <div className="decision">
              <div className="decision__proposal">
                <span className="field__label">proposed action</span>
                <p className="decision__action mono">
                  {diagnosis?.recommended_action ?? 'no action recorded'}
                </p>
                <p className="decision__target mono">
                  {diagnosis?.action_target ?? 'no target recorded'}
                </p>
                <SeverityBadge
                  severity={typeof diagnosis?.risk === 'string' ? diagnosis.risk : 'unknown'}
                />
              </div>
              <p className="decision__copy">
                Approving runs exactly this one action and then verifies recovery. Rejecting aborts
                the run; nothing is executed.
              </p>
              <label className="field__label" htmlFor="decision-reason">
                reason (optional)
              </label>
              <textarea
                id="decision-reason"
                rows={2}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="why you approve or reject this action"
                disabled={busy !== null}
              />
              <div className="decision__buttons">
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void decide('approve')}
                  disabled={busy !== null}
                >
                  {busy === 'approve' ? 'resuming…' : 'Approve'}
                </button>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => void decide('reject')}
                  disabled={busy !== null}
                >
                  {busy === 'reject' ? 'rejecting…' : 'Reject'}
                </button>
              </div>
              <p className="hint">
                approval resumes the run and verification can take up to 60 seconds. The service
                records every manual decision with the reason <span className="mono">operator</span>.
              </p>
            </div>
          ) : (
            <>
              <Fields rows={decisionFields} />
              {!run.approval_status && !run.action_result && !run.verify_result ? (
                <p className="muted">no decision fields have been written yet.</p>
              ) : null}
            </>
          )}
        </Panel>
      ) : null}

      {faultType ? (
        <MemoryPanel faultType={faultType} refreshKey={memoryNonce} />
      ) : (
        <Panel title="memory">
          <EmptyState title="fault type not recorded">
            Incident memory is keyed by fault type, and this run has not written one yet.
          </EmptyState>
        </Panel>
      )}
    </>
  );
}
