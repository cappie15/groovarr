import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';

/**
 * Small shared fetch-state hook (loading/data/error + a manual `refetch`) so
 * pages don't each hand-roll the same `useEffect` + `useState` boilerplate.
 * Deliberately minimal — no caching/dedup layer (React Query, etc.) since a
 * handful of pages hitting a handful of endpoints doesn't need one yet; add
 * one later if that stops being true.
 */
export function useApiQuery<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const run = useCallback((signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetcherRef
      .current(signal)
      .then((result) => {
        if (signal.aborted) return;
        setData(result);
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        setError(err instanceof ApiError ? err.message : 'Unexpected error loading data.');
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false);
      });
  }, []);

  const [refetchToken, setRefetchToken] = useState(0);
  const refetch = useCallback(() => setRefetchToken((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    run(controller.signal);
    return () => controller.abort();
    // `deps` is caller-controlled and intentionally spread into this array —
    // this hook can't know its shape ahead of time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, refetchToken, ...deps]);

  return { data, loading, error, refetch };
}
