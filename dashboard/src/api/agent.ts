import { buildQuery, request } from './client';
import type {
  AgentRun,
  IncidentMemory,
  RunList,
  StartRunAccepted,
  StartRunRequest,
} from './types';

const BASE = '/api/agent';

export function listRuns(status?: string): Promise<RunList> {
  return request(`${BASE}/runs${buildQuery({ status })}`);
}

export function getRun(scenarioId: string): Promise<AgentRun> {
  return request(`${BASE}/runs/${encodeURIComponent(scenarioId)}`);
}

/** Accepts 202 with `{scenario_id, status}`; the run continues in the background. */
export function startRun(body: StartRunRequest): Promise<StartRunAccepted> {
  return request(`${BASE}/runs`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

function decide(path: 'approve' | 'reject', scenarioId: string, reason?: string) {
  const trimmed = reason?.trim();
  return request<AgentRun>(`${BASE}/runs/${encodeURIComponent(scenarioId)}/${path}`, {
    method: 'POST',
    ...(trimmed ? { body: JSON.stringify({ reason: trimmed }) } : {}),
  });
}

export function approveRun(scenarioId: string, reason?: string): Promise<AgentRun> {
  return decide('approve', scenarioId, reason);
}

export function rejectRun(scenarioId: string, reason?: string): Promise<AgentRun> {
  return decide('reject', scenarioId, reason);
}

export function getMemory(faultType: string): Promise<IncidentMemory> {
  return request(`${BASE}/memory/${encodeURIComponent(faultType)}`);
}
