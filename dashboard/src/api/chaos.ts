import { buildQuery, request } from './client';
import type {
  Scenario,
  ScenarioCreateRequest,
  ScenarioList,
  ScenarioStatus,
} from './types';

const BASE = '/api/chaos';

export function listScenarios(status?: ScenarioStatus): Promise<ScenarioList> {
  return request(`${BASE}/scenarios${buildQuery({ status })}`);
}

export function getScenario(id: string): Promise<Scenario> {
  return request(`${BASE}/scenarios/${encodeURIComponent(id)}`);
}

export function createScenario(body: ScenarioCreateRequest): Promise<Scenario> {
  return request(`${BASE}/scenarios`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function stopScenario(id: string): Promise<Scenario> {
  return request(`${BASE}/scenarios/${encodeURIComponent(id)}/stop`, { method: 'POST' });
}

export function removeScenario(id: string): Promise<void> {
  return request(`${BASE}/scenarios/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
