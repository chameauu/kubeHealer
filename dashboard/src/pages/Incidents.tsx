import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { startRun } from '../api/agent';
import { createScenario, listScenarios, removeScenario, stopScenario } from '../api/chaos';
import { toApiError, type ApiError } from '../api/client';
import { listCatalog } from '../api/faultlib';
import { getBlastRadius } from '../api/graph';
import type {
  FaultCatalogEntry,
  K8sMode,
  Scenario,
  ScenarioCreateRequest,
  ScenarioDomain,
  SimResourceType,
} from '../api/types';
import { BlastRadiusPanel, IDLE_BLAST, type BlastState } from '../components/BlastRadiusPanel';
import { EmptyState, LoadingLine } from '../components/EmptyState';
import { ErrorBanner } from '../components/ErrorBanner';
import { Fields, Panel } from '../components/Panel';
import { ScenarioStatusPill } from '../components/StatusPill';
import { TargetSummary } from '../components/TargetSummary';
import { useNow } from '../hooks/useNow';
import { usePoll } from '../hooks/usePoll';
import {
  formatAge,
  formatClock,
  formatTimestamp,
  parseTimestamp,
  plural,
} from '../lib/format';
import { isTerminalStatus } from '../lib/scenario';

const CONFIRM_PHRASE = 'INJECT LIVE FAULT';
const POLL_MS = 2000;
const MODES: K8sMode[] = ['one', 'all', 'fixed', 'fixed-percent', 'random-max-percent'];
const VALUE_MODES: K8sMode[] = ['fixed', 'fixed-percent', 'random-max-percent'];
const SIM_RESOURCES: SimResourceType[] = ['instance', 'volume', 'subnet'];

interface SelectorRow {
  key: string;
  value: string;
}

interface RowActionError {
  error: ApiError;
  title: string;
}

export function Incidents({ onOpenAgent }: { onOpenAgent: (scenarioId: string) => void }) {
  const now = useNow(1000);
  const scenarioPoll = usePoll(() => listScenarios(), POLL_MS);
  const scenarios = scenarioPoll.data?.scenarios ?? [];

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<RowActionError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId && scenarios.length > 0) {
      const first = scenarios[0];
      if (first) setSelectedId(first.id);
    }
  }, [scenarios, selectedId]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!confirmDeleteId) return;
    const timer = window.setTimeout(() => setConfirmDeleteId(null), 4000);
    return () => window.clearTimeout(timer);
  }, [confirmDeleteId]);

  const selected = scenarios.find((scenario) => scenario.id === selectedId) ?? null;

  async function handleStop(scenario: Scenario) {
    setActionError(null);
    setNotice(null);
    setRowBusy(`stop:${scenario.id}`);
    try {
      const updated = await stopScenario(scenario.id);
      setNotice(`stop accepted — ${updated.id} is ${updated.status}`);
      scenarioPoll.refresh();
    } catch (caught) {
      setActionError({
        error: toApiError(caught),
        title: `stop ${scenario.id} was refused`,
      });
      scenarioPoll.refresh();
    } finally {
      setRowBusy(null);
    }
  }

  async function handleDelete(scenario: Scenario) {
    if (confirmDeleteId !== scenario.id) {
      setConfirmDeleteId(scenario.id);
      return;
    }
    setConfirmDeleteId(null);
    setActionError(null);
    setNotice(null);
    setRowBusy(`delete:${scenario.id}`);
    try {
      await removeScenario(scenario.id);
      setNotice(`deleted ${scenario.id}`);
      if (selectedId === scenario.id) setSelectedId(null);
      scenarioPoll.refresh();
    } catch (caught) {
      setActionError({
        error: toApiError(caught),
        title: `delete ${scenario.id} was refused`,
      });
      scenarioPoll.refresh();
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <div className="page page--split">
      <aside className="pane pane--list">
        <div className="pane__head">
          <div>
            <h1 className="pane__title">incidents</h1>
            <p className="pane__meta">
              <span className="mono">{scenarioPoll.data?.total ?? scenarios.length}</span> scenarios
              <span className="dot-sep" aria-hidden="true">
                ·
              </span>
              synced{' '}
              <span className="mono">
                {scenarioPoll.lastUpdatedAt
                  ? formatClock(scenarioPoll.lastUpdatedAt)
                  : scenarioPoll.loading
                    ? 'connecting'
                    : '—'}
              </span>
            </p>
          </div>
          <button
            type="button"
            className="btn btn--ghost btn--xs"
            onClick={scenarioPoll.refresh}
            aria-label="refresh scenarios"
          >
            refresh
          </button>
        </div>

        {scenarioPoll.error ? (
          <ErrorBanner
            error={scenarioPoll.error}
            title="scenario list refresh failed"
            note={
              scenarioPoll.data
                ? 'showing the last successful read.'
                : 'the list is empty because chaos did not answer.'
            }
            onRetry={scenarioPoll.refresh}
          />
        ) : null}

        {actionError ? (
          <ErrorBanner
            error={actionError.error}
            title={actionError.title}
            note="chaos decides transitions; the console only offers the actions the state allows."
          />
        ) : null}

        {notice ? (
          <div className="notice" role="status">
            {notice}
          </div>
        ) : null}

        <div className="pane__scroll">
          {scenarioPoll.loading && !scenarioPoll.data ? (
            <LoadingLine label="reading scenarios" />
          ) : null}

          {!scenarioPoll.loading && scenarioPoll.data && scenarios.length === 0 ? (
            <EmptyState title="no scenarios yet">
              Inject one on the right. A simulator scenario submits directly; a live Chaos Mesh
              scenario needs a blast radius preview and the confirmation phrase.
            </EmptyState>
          ) : null}

          <ul className="scenario-list">
            {scenarios.map((scenario) => {
              const terminal = isTerminalStatus(scenario.status);
              const endMs = terminal
                ? (parseTimestamp(scenario.ended_at) ?? now)
                : now;
              const age = formatAge(scenario.created_at, endMs);
              const isSelected = scenario.id === selectedId;
              const busy = rowBusy === `stop:${scenario.id}` || rowBusy === `delete:${scenario.id}`;

              return (
                <li
                  key={scenario.id}
                  className={`scenario-item${isSelected ? ' is-selected' : ''}`}
                  onClick={() => setSelectedId(scenario.id)}
                >
                  <div className="scenario-item__head">
                    <button
                      type="button"
                      className="scenario-item__pick"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedId(scenario.id);
                      }}
                      aria-pressed={isSelected}
                    >
                      <span className="scenario-item__name">{scenario.name || scenario.id}</span>
                    </button>
                    <ScenarioStatusPill status={scenario.status} />
                    <div className="scenario-item__actions">
                      {scenario.status === 'running' ? (
                        <button
                          type="button"
                          className="row-action row-action--warn"
                          disabled={busy}
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleStop(scenario);
                          }}
                          aria-label={`stop ${scenario.id}`}
                        >
                          {rowBusy === `stop:${scenario.id}` ? 'stopping…' : 'stop'}
                        </button>
                      ) : null}
                      {terminal ? (
                        <button
                          type="button"
                          className={`row-action row-action--danger${
                            confirmDeleteId === scenario.id ? ' is-confirming' : ''
                          }`}
                          disabled={busy}
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleDelete(scenario);
                          }}
                          aria-label={
                            confirmDeleteId === scenario.id
                              ? `confirm delete ${scenario.id}`
                              : `delete ${scenario.id}`
                          }
                        >
                          {rowBusy === `delete:${scenario.id}`
                            ? 'deleting…'
                            : confirmDeleteId === scenario.id
                              ? 'confirm'
                              : 'delete'}
                        </button>
                      ) : null}
                      {scenario.status === 'stopping' ? (
                        <span className="row-note">stopping…</span>
                      ) : null}
                    </div>
                  </div>
                  <div className="scenario-item__meta">
                    <span className="mono">{scenario.fault_type}</span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span className="mono">{scenario.domain}</span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span title={formatTimestamp(scenario.created_at)}>{age}</span>
                    <span className="dot-sep" aria-hidden="true">
                      ·
                    </span>
                    <span className="scenario-item__id mono">{scenario.id}</span>
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
            <SelectedScenario
              scenario={selected}
              onStartRun={(scenario) => onOpenAgent(scenario.id)}
            />
            <Injector onCreated={(created) => {
              setSelectedId(created.id);
              scenarioPoll.refresh();
            }} />
          </div>
        </div>
      </section>
    </div>
  );
}

function SelectedScenario({
  scenario,
  onStartRun,
}: {
  scenario: Scenario | null;
  onStartRun: (scenario: Scenario) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<ApiError | null>(null);

  useEffect(() => {
    setRunError(null);
  }, [scenario?.id]);

  if (!scenario) {
    return (
      <Panel title="selected scenario">
        <EmptyState title="no scenario selected">
          Pick a scenario on the left to read its target and hand it to the agent.
        </EmptyState>
      </Panel>
    );
  }

  const params = scenario.params ?? {};
  const hasParams = Object.keys(params).length > 0;

  async function diagnose() {
    if (!scenario) return;
    setBusy(true);
    setRunError(null);
    try {
      await startRun({ scenario_id: scenario.id, scenario: { ...scenario } });
      onStartRun(scenario);
    } catch (caught) {
      setRunError(toApiError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title={
        <span>
          selected <span className="panel__subject mono">{scenario.id}</span>
        </span>
      }
      meta={<ScenarioStatusPill status={scenario.status} />}
      actions={
        <button
          type="button"
          className="btn btn--primary btn--xs"
          onClick={() => void diagnose()}
          disabled={busy}
          title="start the detect → diagnose → approval pipeline for this scenario"
        >
          {busy ? 'starting…' : 'start diagnosis'}
        </button>
      }
    >
      <h3 className="selected-name">{scenario.name || scenario.id}</h3>
      <div className="selected-line">
        <span className="mono">{scenario.fault_type}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span className="mono">{scenario.domain}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <span>created {formatTimestamp(scenario.created_at)}</span>
        <span className="dot-sep" aria-hidden="true">
          ·
        </span>
        <TargetSummary target={scenario.target} />
      </div>

      <details className="raw-block selected-record">
        <summary>scenario record</summary>
        <Fields
          rows={[
            { label: 'fault type', value: scenario.fault_type, mono: true },
            { label: 'domain', value: scenario.domain, mono: true },
            {
              label: 'target',
              value: <TargetSummary target={scenario.target} />,
              wide: true,
            },
            {
              label: 'created',
              value: formatTimestamp(scenario.created_at),
              mono: true,
            },
            {
              label: 'started',
              value: formatTimestamp(scenario.started_at),
              mono: true,
            },
            {
              label: 'ended',
              value: formatTimestamp(scenario.ended_at),
              mono: true,
            },
            {
              label: 'duration',
              value:
                scenario.duration_seconds === null
                  ? 'not set'
                  : `${scenario.duration_seconds}s`,
              mono: true,
            },
            {
              label: 'backend ref',
              value: scenario.backend_ref ?? 'not assigned',
              mono: true,
            },
          ]}
        />

        {hasParams ? (
          <div className="params">
            <span className="field__label">params</span>
            <pre className="code-block">{JSON.stringify(params, null, 2)}</pre>
          </div>
        ) : null}

        {scenario.error ? (
          <div className="inline-error">
            <span className="inline-error__label">scenario error</span>
            <p>{scenario.error}</p>
          </div>
        ) : null}
      </details>

      <p className="panel__note">
        stop only while running; delete only in a terminal state. A raced or stale click is reported
        by chaos, not hidden.
      </p>

      {runError ? (
        <ErrorBanner
          error={runError}
          title="the agent did not accept this run"
          onRetry={() => void diagnose()}
        />
      ) : null}
    </Panel>
  );
}

function Injector({ onCreated }: { onCreated: (scenario: Scenario) => void }) {
  const [domain, setDomain] = useState<ScenarioDomain>('simulator');
  const [name, setName] = useState('');
  const [faultType, setFaultType] = useState('');
  const [namespace, setNamespace] = useState('');
  const [selectorRows, setSelectorRows] = useState<SelectorRow[]>([{ key: 'app', value: '' }]);
  const [mode, setMode] = useState<K8sMode>('one');
  const [modeValue, setModeValue] = useState('');
  const [simResource, setSimResource] = useState<SimResourceType>('instance');
  const [simOperation, setSimOperation] = useState('');
  const [duration, setDuration] = useState('');
  const [probability, setProbability] = useState('');

  const [catalog, setCatalog] = useState<FaultCatalogEntry[]>([]);
  const [catalogError, setCatalogError] = useState<ApiError | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogNonce, setCatalogNonce] = useState(0);

  const [blast, setBlast] = useState<BlastState>(IDLE_BLAST);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [phrase, setPhrase] = useState('');
  const [showProblems, setShowProblems] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [formNotice, setFormNotice] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setCatalogLoading(true);
    setCatalogError(null);
    listCatalog(domain)
      .then((response) => {
        if (!alive) return;
        setCatalog(response.entries);
        setFaultType((current) =>
          response.entries.some((entry) => entry.fault_type === current)
            ? current
            : (response.entries[0]?.fault_type ?? ''),
        );
      })
      .catch((caught: unknown) => {
        if (!alive) return;
        setCatalog([]);
        setCatalogError(toApiError(caught));
      })
      .finally(() => {
        if (alive) setCatalogLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [domain, catalogNonce]);

  const selectorObject = useMemo(() => {
    const result: Record<string, string> = {};
    for (const row of selectorRows) {
      const key = row.key.trim();
      if (!key) continue;
      result[key] = row.value.trim();
    }
    return result;
  }, [selectorRows]);

  const blastKey = useMemo(() => {
    if (domain !== 'chaos_mesh' || !namespace.trim() || !faultType) return '';
    return JSON.stringify({
      namespace: namespace.trim(),
      faultType,
      selector: selectorObject,
    });
  }, [domain, namespace, faultType, selectorObject]);

  useEffect(() => {
    if (!blastKey) {
      setBlast(IDLE_BLAST);
      return;
    }
    let alive = true;
    const timer = window.setTimeout(() => {
      setBlast((previous) => ({
        ...previous,
        status: 'loading',
        key: blastKey,
        error: null,
      }));
      getBlastRadius({
        target_namespace: namespace.trim(),
        fault_type: faultType,
        selector: selectorObject,
      })
        .then((data) => {
          if (!alive) return;
          setBlast({ status: 'ready', key: blastKey, data, error: null, at: Date.now() });
        })
        .catch((caught: unknown) => {
          if (!alive) return;
          setBlast({
            status: 'error',
            key: blastKey,
            data: null,
            error: toApiError(caught),
            at: null,
          });
        });
    }, 400);

    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [blastKey, previewNonce, namespace, faultType, selectorObject]);

  const entry = catalog.find((item) => item.fault_type === faultType) ?? null;
  const phraseOk = phrase === CONFIRM_PHRASE;
  const previewFresh =
    blast.status === 'ready' && blast.key === blastKey && blast.data !== null;

  const problems = useMemo(() => {
    const found: string[] = [];
    if (!name.trim()) found.push('name is required');

    if (!faultType) found.push('choose a fault type');

    if (domain === 'chaos_mesh') {
      if (!namespace.trim()) found.push('namespace is required');
      const seen = new Set<string>();
      selectorRows.forEach((row, index) => {
        const key = row.key.trim();
        const value = row.value.trim();
        if (!key && !value) return;
        if (!key) {
          found.push(`selector row ${index + 1} has a value but no key`);
          return;
        }
        if (!value) found.push(`selector "${key}" has no value`);
        if (seen.has(key)) found.push(`selector "${key}" is repeated`);
        seen.add(key);
      });
      if (VALUE_MODES.includes(mode)) {
        const raw = modeValue.trim();
        if (!raw) {
          found.push(`mode ${mode} needs a value`);
        } else if (mode === 'fixed') {
          if (!/^[1-9]\d*$/.test(raw)) found.push('fixed mode needs a whole number of pods');
        } else {
          const value = Number(raw);
          if (!Number.isInteger(value) || value < 1 || value > 100) {
            found.push(`${mode} needs a whole number between 1 and 100`);
          }
        }
      }
    } else {
      if (!simResource) found.push('choose a simulator resource type');
      if (!simOperation.trim()) found.push('operation is required for a simulator target');
    }

    if (duration.trim()) {
      const value = Number(duration);
      if (!Number.isFinite(value) || value <= 0) {
        found.push('duration must be a positive number of seconds');
      }
    }
    if (probability.trim()) {
      const value = Number(probability);
      if (!Number.isFinite(value) || value < 0 || value > 1) {
        found.push('probability must be between 0 and 1');
      }
    }

    return found;
  }, [
    name,
    faultType,
    domain,
    namespace,
    selectorRows,
    mode,
    modeValue,
    simResource,
    simOperation,
    duration,
    probability,
  ]);

  const submitDisabled =
    submitting ||
    catalogLoading ||
    Boolean(catalogError) ||
    catalog.length === 0 ||
    (domain === 'chaos_mesh' && (!phraseOk || !previewFresh));

  const submitTitle =
    domain === 'chaos_mesh'
      ? !phraseOk
        ? `type ${CONFIRM_PHRASE} exactly to enable live injection`
        : !previewFresh
          ? 'wait for a current blast radius preview'
          : 'inject this live fault'
      : 'inject this simulator fault';

  function updateSelector(index: number, patch: Partial<SelectorRow>) {
    setSelectorRows((rows) =>
      rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setShowProblems(true);
    setFormError(null);
    setFormNotice(null);

    if (problems.length > 0) return;
    if (domain === 'chaos_mesh' && (!phraseOk || !previewFresh)) return;

    const target: Record<string, unknown> =
      domain === 'chaos_mesh'
        ? {
            namespace: namespace.trim(),
            label_selector: selectorObject,
            mode,
            ...(VALUE_MODES.includes(mode) ? { value: modeValue.trim() } : {}),
          }
        : {
            resource_type: simResource,
            operation: simOperation.trim(),
          };

    const body: ScenarioCreateRequest = {
      name: name.trim(),
      domain,
      fault_type: faultType,
      target,
      ...(duration.trim() ? { duration_seconds: Number(duration) } : {}),
      ...(probability.trim() ? { probability: Number(probability) } : {}),
    };

    setSubmitting(true);
    try {
      const created = await createScenario(body);
      setFormNotice(
        `${created.id} created — ${created.status}. It is selected in the list.`,
      );
      setName('');
      setPhrase('');
      setShowProblems(false);
      onCreated(created);
    } catch (caught) {
      setFormError(toApiError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Panel
      title="inject a fault"
      meta={<span className="panel__meta-text">safe submits directly · live needs a preview and the phrase</span>}
      className="panel--inject"
    >
      <form className="form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="field">
          <label className="field__label" htmlFor="fault-name">
            name
          </label>
          <input
            id="fault-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="checkout pod kill"
            autoComplete="off"
          />
          <p className="hint">recorded on the scenario and shown in the incident list.</p>
        </div>

        <fieldset className="field domain-field">
          <legend className="field__label">domain</legend>
          <div className="domain-tiles">
            <label className={`domain-tile${domain === 'simulator' ? ' is-active' : ''}`}>
              <input
                type="radio"
                name="domain"
                value="simulator"
                checked={domain === 'simulator'}
                onChange={() => setDomain('simulator')}
              />
              <span className="domain-tile__tag">safe</span>
              <span className="domain-tile__name">simulator</span>
              <span className="domain-tile__note">
                faults the fake cloud. no cluster write; submits directly.
              </span>
            </label>
            <label className={`domain-tile domain-tile--live${domain === 'chaos_mesh' ? ' is-active' : ''}`}>
              <input
                type="radio"
                name="domain"
                value="chaos_mesh"
                checked={domain === 'chaos_mesh'}
                onChange={() => setDomain('chaos_mesh')}
              />
              <span className="domain-tile__tag">live</span>
              <span className="domain-tile__name">chaos mesh</span>
              <span className="domain-tile__note">
                writes a Chaos Mesh experiment to the cluster. previewed and armed.
              </span>
            </label>
          </div>
        </fieldset>

        <div className="field">
          <label className="field__label" htmlFor="fault-type">
            fault type
          </label>
          <select
            id="fault-type"
            value={faultType}
            onChange={(event) => setFaultType(event.target.value)}
            disabled={catalogLoading || catalog.length === 0 || Boolean(catalogError)}
          >
            {catalog.length === 0 ? (
              <option value="">
                {catalogLoading ? 'loading catalogue…' : 'no entries for this domain'}
              </option>
            ) : null}
            {catalog.map((item) => (
              <option key={item.fault_type} value={item.fault_type}>
                {item.fault_type} · {item.taxonomy_category}
              </option>
            ))}
          </select>
          {catalogError ? (
            <ErrorBanner
              error={catalogError}
              title="could not read the fault catalogue"
              note="injection stays disabled without a catalogue entry for the domain."
              onRetry={() => setCatalogNonce((value) => value + 1)}
            />
          ) : null}
          {entry ? (
            <p className="catalogue-note">
              {entry.description}
              <span className="catalogue-note__shape">
                blast radius shape: <span className="mono">{entry.blast_radius_shape}</span>
              </span>
            </p>
          ) : null}
        </div>

        {domain === 'chaos_mesh' ? (
          <>
            <div className="field">
              <label className="field__label" htmlFor="namespace">
                namespace
              </label>
              <input
                id="namespace"
                value={namespace}
                onChange={(event) => setNamespace(event.target.value)}
                placeholder="edge"
                autoComplete="off"
                spellCheck={false}
              />
            </div>

            <fieldset className="field selector-field">
              <legend className="field__label">label selector</legend>
              <div className="selector-rows">
                {selectorRows.map((row, index) => (
                  <div className="selector-row" key={index}>
                    <input
                      aria-label={`selector key ${index + 1}`}
                      placeholder="app"
                      value={row.key}
                      onChange={(event) => updateSelector(index, { key: event.target.value })}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <span className="selector-row__eq" aria-hidden="true">
                      =
                    </span>
                    <input
                      aria-label={`selector value ${index + 1}`}
                      placeholder="api"
                      value={row.value}
                      onChange={(event) => updateSelector(index, { value: event.target.value })}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <button
                      type="button"
                      className="btn btn--ghost btn--xs"
                      onClick={() =>
                        setSelectorRows((rows) => rows.filter((_, rowIndex) => rowIndex !== index))
                      }
                      aria-label={`remove selector row ${index + 1}`}
                    >
                      remove
                    </button>
                  </div>
                ))}
              </div>
              <div className="selector-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--xs"
                  onClick={() => setSelectorRows((rows) => [...rows, { key: '', value: '' }])}
                >
                  add label
                </button>
                <span className="hint">
                  {Object.keys(selectorObject).length === 0
                    ? 'no labels: every pod in the namespace matches.'
                    : 'sent to graph as one selector=key=value per label, sorted by key.'}
                </span>
              </div>
            </fieldset>

            <div className="field-grid">
              <div className="field">
                <label className="field__label" htmlFor="mode">
                  mode
                </label>
                <select
                  id="mode"
                  value={mode}
                  onChange={(event) => setMode(event.target.value as K8sMode)}
                >
                  {MODES.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              {VALUE_MODES.includes(mode) ? (
                <div className="field">
                  <label className="field__label" htmlFor="mode-value">
                    value
                  </label>
                  <input
                    id="mode-value"
                    value={modeValue}
                    onChange={(event) => setModeValue(event.target.value)}
                    placeholder={mode === 'fixed' ? '2' : '50'}
                    inputMode="numeric"
                    autoComplete="off"
                  />
                  <p className="hint">
                    {mode === 'fixed'
                      ? 'whole number of pods.'
                      : mode === 'fixed-percent'
                        ? 'percent of pods, 1–100.'
                        : 'upper bound percent, 1–100.'}
                  </p>
                </div>
              ) : null}
            </div>

            <BlastRadiusPanel
              state={blast}
              stale={Boolean(blastKey) && blast.key !== blastKey}
              onRetry={() => setPreviewNonce((value) => value + 1)}
              synthetic={blast.data?.topology_sources.includes('synthetic_fixture') ?? false}
            />
          </>
        ) : (
          <div className="field-grid">
            <div className="field">
              <label className="field__label" htmlFor="sim-resource">
                resource type
              </label>
              <select
                id="sim-resource"
                value={simResource}
                onChange={(event) => setSimResource(event.target.value as SimResourceType)}
              >
                {SIM_RESOURCES.map((resource) => (
                  <option key={resource} value={resource}>
                    {resource}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field__label" htmlFor="sim-operation">
                operation
              </label>
              <input
                id="sim-operation"
                value={simOperation}
                onChange={(event) => setSimOperation(event.target.value)}
                placeholder="attach"
                list="sim-operations"
                autoComplete="off"
                spellCheck={false}
              />
              <datalist id="sim-operations">
                <option value="attach" />
                <option value="detach" />
                <option value="provision" />
                <option value="create" />
                <option value="delete" />
              </datalist>
              <p className="hint">
                the simulator matches this resource type and operation when it applies the fault.
              </p>
            </div>
          </div>
        )}

        <div className="field-grid">
          <div className="field">
            <label className="field__label" htmlFor="duration">
              duration (seconds)
            </label>
            <input
              id="duration"
              type="number"
              min="1"
              step="1"
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
              placeholder="optional"
            />
            <p className="hint">passed to the backend; optional.</p>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="probability">
              probability
            </label>
            <input
              id="probability"
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={probability}
              onChange={(event) => setProbability(event.target.value)}
              placeholder="1.0 default"
            />
            <p className="hint">0–1; omitted means 1.0.</p>
          </div>
        </div>

        {domain === 'chaos_mesh' ? (
          <section className="confirm-block">
            <div className="confirm-block__head">
              <span className="confirm-block__tag">live</span>
              <h3>arm the injection</h3>
            </div>
            <p className="confirm-block__copy">
              Chaos Mesh writes a real experiment. The submit button stays disabled until this exact
              phrase is typed and the blast radius above is current for this target.
            </p>
            <label className="field__label" htmlFor="confirm-phrase">
              type {CONFIRM_PHRASE}
            </label>
            <input
              id="confirm-phrase"
              className={`mono confirm-input${phraseOk ? ' is-armed' : ''}`}
              value={phrase}
              onChange={(event) => setPhrase(event.target.value)}
              placeholder={CONFIRM_PHRASE}
              autoComplete="off"
              spellCheck={false}
              aria-describedby="confirm-gates"
            />
            <ul className="gate-list" id="confirm-gates">
              <li className={previewFresh ? 'is-ok' : 'is-waiting'}>
                <span className="gate-mark" aria-hidden="true">
                  {previewFresh ? '✓' : '·'}
                </span>
                blast radius{' '}
                {previewFresh && blast.data
                  ? `previewed — ${plural(blast.data.affected_nodes.length, 'affected node')}`
                  : 'not current for this target'}
              </li>
              <li className={phraseOk ? 'is-ok' : 'is-waiting'}>
                <span className="gate-mark" aria-hidden="true">
                  {phraseOk ? '✓' : '·'}
                </span>
                confirmation {phraseOk ? 'matched' : `not typed yet`}
              </li>
            </ul>
          </section>
        ) : null}

        {showProblems && problems.length > 0 ? (
          <div className="problems" role="alert">
            <span className="problems__title">fix before submitting</span>
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {formError ? (
          <ErrorBanner
            error={formError}
            title="chaos rejected the scenario"
            note="the message above is the upstream detail, unchanged."
          />
        ) : null}

        {formNotice ? (
          <div className="notice" role="status">
            {formNotice}
          </div>
        ) : null}

        <div className="submit-row">
          <button
            type="submit"
            className={`btn btn--primary${domain === 'chaos_mesh' ? ' btn--live' : ''}`}
            disabled={submitDisabled}
            title={submitTitle}
          >
            {submitting
              ? 'submitting…'
              : domain === 'chaos_mesh'
                ? 'inject live fault'
                : 'inject simulator fault'}
          </button>
          <span className="hint">
            {domain === 'chaos_mesh'
              ? 'two gates: a current blast radius and the exact confirmation phrase.'
              : 'simulator scenarios submit directly; nothing is written to the cluster.'}
          </span>
        </div>
      </form>
    </Panel>
  );
}
