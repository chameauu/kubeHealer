import type { ApiError } from '../api/client';
import type { BlastRadiusResponse } from '../api/types';
import { formatAge, plural } from '../lib/format';
import { EmptyState, LoadingLine } from './EmptyState';
import { ErrorBanner } from './ErrorBanner';
import { SeverityBadge } from './StatusPill';

export interface BlastState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  key: string;
  data: BlastRadiusResponse | null;
  error: ApiError | null;
  at: number | null;
}

export const IDLE_BLAST: BlastState = {
  status: 'idle',
  key: '',
  data: null,
  error: null,
  at: null,
};

export function BlastRadiusPanel({
  state,
  stale,
  onRetry,
  synthetic,
}: {
  state: BlastState;
  stale: boolean;
  onRetry: () => void;
  synthetic: boolean;
}) {
  const data = state.data;
  const statusText =
    state.status === 'idle'
      ? 'waiting for namespace and fault type'
      : state.status === 'loading'
        ? stale
          ? 'recomputing'
          : 'computing'
        : state.status === 'error'
          ? 'unavailable'
          : stale
            ? 'stale'
            : 'current';

  return (
    <section className={`blast blast--${state.status}${stale ? ' is-stale' : ''}`}>
      <header className="blast__head">
        <h3 className="blast__title">blast radius</h3>
        <span className="blast__status">{statusText}</span>
        <button type="button" className="btn btn--ghost btn--xs" onClick={onRetry}>
          recompute
        </button>
      </header>

      {state.error ? (
        <ErrorBanner
          error={state.error}
          title="blast radius unavailable"
          note="Live injection stays disabled until graph answers for this target."
          onRetry={onRetry}
        />
      ) : null}

      {state.status === 'loading' && !data ? <LoadingLine label="querying graph" /> : null}

      {state.status === 'idle' ? (
        <p className="blast__hint">
          A live scenario is previewed before it runs: namespace, fault type and label selector are
          sent to graph, and the affected services are listed here.
        </p>
      ) : null}

      {stale && data ? (
        <p className="blast__stale">target changed — this preview belongs to the previous input.</p>
      ) : null}

      {data ? (
        <>
          <div className="blast__summary">
            <span className="mono">
              {data.target_namespace} / {data.fault_type}
            </span>
            <span className="dot-sep" aria-hidden="true">
              ·
            </span>
            <span>{plural(data.affected_nodes.length, 'affected node')}</span>
            <span className="dot-sep" aria-hidden="true">
              ·
            </span>
            <span>{plural(data.matched_nodes.length, 'matched node')}</span>
            {state.at ? (
              <>
                <span className="dot-sep" aria-hidden="true">
                  ·
                </span>
                <span className="muted">computed {formatAge(new Date(state.at).toISOString(), Date.now())} ago</span>
              </>
            ) : null}
          </div>

          <div className="tag-row">
            {data.topology_sources.map((source) => (
              <span
                key={source}
                className={`tag${source === 'synthetic_fixture' ? ' tag--synthetic' : ''}`}
              >
                {source}
              </span>
            ))}
            {data.target_selector && Object.keys(data.target_selector).length > 0 ? (
              Object.entries(data.target_selector)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([key, value]) => (
                  <span className="tag tag--mono" key={key}>
                    {key}={value}
                  </span>
                ))
            ) : (
              <span className="tag">no label selector — whole namespace</span>
            )}
          </div>

          {synthetic ? (
            <p className="synthetic-note">
              demo topology: the source includes a synthetic fixture, so this is not a measured
              cluster.
            </p>
          ) : null}

          {data.affected_nodes.length === 0 ? (
            <EmptyState title="no affected nodes returned">
              Graph answered for this target but reached nothing. The topology may be empty, or the
              selector may match no service.
            </EmptyState>
          ) : (
            <div className="table-scroll table-scroll--short">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th scope="col">node</th>
                    <th scope="col">namespace</th>
                    <th scope="col" className="num">
                      hops
                    </th>
                    <th scope="col">severity</th>
                    <th scope="col">via</th>
                  </tr>
                </thead>
                <tbody>
                  {data.affected_nodes.map((node) => (
                    <tr key={node.node_id}>
                      <td className="mono truncate" title={node.node_id}>
                        {node.name}
                      </td>
                      <td className="mono">{node.namespace}</td>
                      <td className="mono num">{node.distance_hops}</td>
                      <td>
                        <SeverityBadge severity={node.severity} />
                      </td>
                      <td className="mono muted truncate">
                        {node.via_edge_types?.length ? node.via_edge_types.join(', ') : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.matched_nodes.length > 0 ? (
            <p className="blast__matched">
              matched <span className="mono">{data.matched_nodes.join(', ')}</span>
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
