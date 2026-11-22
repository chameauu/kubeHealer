import { buildQuery, request } from './client';
import type { BlastRadiusQuery, BlastRadiusResponse, TopologyResponse } from './types';

const BASE = '/api/graph';

export function getTopology(): Promise<TopologyResponse> {
  return request(`${BASE}/topology`);
}

/**
 * `selector` is repeated, once per label, and sorted by key so the same
 * selector always produces the same request (contract rule for agent → graph).
 */
export function getBlastRadius(query: BlastRadiusQuery): Promise<BlastRadiusResponse> {
  const selector = Object.keys(query.selector)
    .sort()
    .map((key) => `${key}=${query.selector[key]}`);

  return request(
    `${BASE}/blast-radius${buildQuery({
      target_namespace: query.target_namespace,
      fault_type: query.fault_type,
      selector,
    })}`,
  );
}
