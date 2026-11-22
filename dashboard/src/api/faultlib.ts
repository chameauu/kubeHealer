import { buildQuery, request } from './client';
import type {
  CatalogResponse,
  FaultCatalogEntry,
  RankingsResponse,
  ScenarioDomain,
} from './types';

const BASE = '/api/faultlib';

export function listCatalog(domain?: ScenarioDomain): Promise<CatalogResponse> {
  return request(`${BASE}/catalog${buildQuery({ domain })}`);
}

export function getCatalogEntry(
  domain: ScenarioDomain,
  faultType: string,
): Promise<FaultCatalogEntry> {
  return request(
    `${BASE}/catalog/${encodeURIComponent(domain)}/${encodeURIComponent(faultType)}`,
  );
}

export function getRankings(): Promise<RankingsResponse> {
  return request(`${BASE}/rankings`);
}
