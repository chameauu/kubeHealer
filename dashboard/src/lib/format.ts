/** Shared formatting. Timestamps and durations are always mono-rendered. */

const MS = 1000;

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

export function parseTimestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

/** `2026-11-22 21:34:02Z` — compact, UTC, second precision. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const ms = parseTimestamp(value);
  if (ms === null) return value;
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

export function formatClock(nowMs: number): string {
  const d = new Date(nowMs);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
}

/** Coarse age, e.g. `8s`, `4m 12s`, `2h 07m`, `3d 4h`. */
export function formatAge(value: string | null | undefined, nowMs: number): string {
  const start = parseTimestamp(value);
  if (start === null) return '—';
  const seconds = Math.max(0, Math.floor((nowMs - start) / MS));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${pad(seconds % 60)}s`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours}h ${pad(Math.floor((seconds % 3600) / 60))}m`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const absolute = Math.abs(seconds);
  if (absolute < 1) return `${Math.round(seconds * 1000)} ms`;
  if (absolute < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  if (whole < 3600) {
    return `${Math.floor(whole / 60)}m ${pad(whole % 60)}s`;
  }
  if (whole < 86400) {
    return `${Math.floor(whole / 3600)}h ${pad(Math.floor((whole % 3600) / 60))}m`;
  }
  return `${Math.floor(whole / 86400)}d ${Math.floor((whole % 86400) / 3600)}h`;
}

/** `success_rate` is a 0–1 fraction; `confidence` is already 0–100. */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—';
  const percent = rate * 100;
  return `${percent >= 100 ? '100' : percent.toFixed(1)}%`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${Math.round(value)}%`;
}

export function shortHash(hash: string | undefined): string {
  if (!hash) return '—';
  return hash.length > 12 ? hash.slice(0, 12) : hash;
}

export function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
