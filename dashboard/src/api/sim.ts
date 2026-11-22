import { request } from './client';
import type {
  SimInstanceList,
  SimState,
  SimSubnetList,
  SimVolumeList,
} from './types';

const BASE = '/api/sim';

export function getState(): Promise<SimState> {
  return request(`${BASE}/state`);
}

export function getVolumes(): Promise<SimVolumeList> {
  return request(`${BASE}/volumes`);
}

export function getSubnets(): Promise<SimSubnetList> {
  return request(`${BASE}/subnets`);
}

export function getInstances(): Promise<SimInstanceList> {
  return request(`${BASE}/instances`);
}
