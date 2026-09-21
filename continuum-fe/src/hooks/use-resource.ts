import { useCallback, useEffect, useRef, useState } from 'react'

export interface Resource<T> {
  data: T | null
  error: string | null
  loading: boolean
  /** Re-run the fetch. Keeps the previous data visible while it is in flight. */
  refresh: () => void
}

/**
 * Minimal async resource hook.
 *
 * Deliberately not TanStack Query: three endpoints, no cache sharing, no
 * pagination and no optimistic writes. Adding a data-fetching framework here
 * would be the frontend's version of the mistake the backend avoided by owning
 * its Qdrant access.
 *
 * What it does handle is the bit that actually bites: a slow response from a
 * previous key arriving after a newer one and clobbering it.
 */
export function useResource<T>(fetcher: () => Promise<T>, deps: unknown[]): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // Only the newest request may write state.
  const generation = useRef(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    const current = ++generation.current
    setLoading(true)

    fetcherRef
      .current()
      .then((result) => {
        if (generation.current !== current) return
        setData(result)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (generation.current !== current) return
        setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (generation.current === current) setLoading(false)
      })
    // `fetcher` is intentionally excluded: it is an inline closure that changes
    // identity every render, and the caller declares its real inputs in `deps`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  return { data, error, loading, refresh }
}
