import { useCallback, useEffect, useRef, useState } from 'react';
import { toApiError, type ApiError } from '../api/client';

export interface PollResult<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  lastUpdatedAt: number | null;
  refresh: () => void;
}

/**
 * Poll one endpoint on an interval. The previous value is kept while a
 * refresh fails, so a blip shows an inline error instead of emptying the page.
 * Polling pauses while the tab is hidden and resumes on visibility.
 */
export function usePoll<T>(fetcher: () => Promise<T>, intervalMs = 2000): PollResult<T> {
  const fetcherRef = useRef(fetcher);
  const hasData = useRef(false);
  const [nonce, setNonce] = useState(0);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    let alive = true;
    let inFlight = false;

    const run = async () => {
      if (document.hidden || inFlight) return;
      inFlight = true;
      try {
        const value = await fetcherRef.current();
        if (!alive) return;
        hasData.current = true;
        setData(value);
        setError(null);
        setLastUpdatedAt(Date.now());
      } catch (caught) {
        if (alive) setError(toApiError(caught));
      } finally {
        inFlight = false;
        if (alive) setLoading(false);
      }
    };

    if (!hasData.current) setLoading(true);
    void run();
    const timer = window.setInterval(() => void run(), intervalMs);

    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, nonce]);

  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) setNonce((value) => value + 1);
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  return { data, error, loading, lastUpdatedAt, refresh };
}
