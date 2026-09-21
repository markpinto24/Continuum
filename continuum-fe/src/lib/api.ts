/**
 * Thin typed client over the Continuum API.
 *
 * Same principle the backend applies to Qdrant: own the ~100 lines rather than
 * take a data-fetching framework's abstraction. Nothing here makes a policy
 * decision — it builds a URL, checks the status, and returns a typed payload.
 */

import type {
  ChatContext,
  ChatDone,
  ChatMessage,
  ConflictListResponse,
  ConflictResolutionRequest,
  ConflictResolutionResponse,
  GraphResponse,
  HealthResponse,
  IngestResponse,
  Memory,
  MemoryListResponse,
} from './types'
import { createSSEParser } from './sse'

/** Empty by default: Vite proxies /api in dev, nginx does it in Docker. */
const BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')
const PREFIX = `${BASE}/api/v1`

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${PREFIX}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status)
  }
  return (await response.json()) as T
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const { detail } = body as { detail: unknown }
      if (typeof detail === 'string') return detail
      return JSON.stringify(detail)
    }
  } catch {
    // Not JSON — fall through to the status line.
  }
  return `${response.status} ${response.statusText}`
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  graph: (userId: string, opts: { limit?: number; includeArchived?: boolean } = {}) =>
    request<GraphResponse>(
      `/memories/graph?${new URLSearchParams({
        user_id: userId,
        limit: String(opts.limit ?? 500),
        include_archived: String(opts.includeArchived ?? false),
      })}`,
    ),

  memory: (id: string) => request<Memory>(`/memories/${id}`),

  memories: (userId: string, limit = 500) =>
    request<MemoryListResponse>(
      `/memories?${new URLSearchParams({ user_id: userId, limit: String(limit) })}`,
    ),

  /** Confirm a memory is still true. Raises confidence, resets the decay clock. */
  reinforce: (id: string) => request<Memory>(`/memories/${id}/reinforce`, { method: 'POST' }),

  /** Bring an archived or superseded memory back. Nothing here is a one-way door. */
  reactivate: (id: string) => request<Memory>(`/memories/${id}/reactivate`, { method: 'POST' }),

  conflicts: (userId: string, limit = 100) =>
    request<ConflictListResponse>(
      `/conflicts?${new URLSearchParams({ user_id: userId, limit: String(limit) })}`,
    ),

  resolveConflict: (body: ConflictResolutionRequest) =>
    request<ConflictResolutionResponse>('/conflicts/resolve', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  ingest: (userId: string, text: string) =>
    request<IngestResponse>('/ingest', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, text }),
    }),

  chatContext: (userId: string, query: string) =>
    request<ChatContext>('/chat/context', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, query }),
    }),
}

// --- Chat streaming ---------------------------------------------------------

export interface ChatHandlers {
  /** Retrieved memories + disagreements. Arrives before the first token. */
  onContext?: (context: ChatContext) => void
  onDelta?: (text: string) => void
  onDone?: (done: ChatDone) => void
  onError?: (message: string) => void
}

/**
 * Stream `POST /chat`, dispatching each SSE frame to a handler.
 *
 * The `context` frame lands before any `delta`, which is the whole reason the
 * backend sends it separately: the UI can show which memories are in play — and
 * flag a dispute — while the answer is still arriving.
 */
export async function streamChat(
  body: { user_id: string; messages: ChatMessage[]; remember?: boolean; limit?: number },
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${PREFIX}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status)
  }
  if (!response.body) {
    throw new ApiError('The chat response carried no body to stream.', 500)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parse = createSSEParser()

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // `stream: true` keeps a multi-byte character split across two network
      // chunks from decoding into a replacement character.
      for (const frame of parse(decoder.decode(value, { stream: true }))) {
        dispatch(frame.event, frame.data, handlers)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function dispatch(event: string, data: string, handlers: ChatHandlers): void {
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    handlers.onError?.(`Unparseable ${event} frame from the server.`)
    return
  }

  switch (event) {
    case 'context':
      handlers.onContext?.(payload as ChatContext)
      break
    case 'delta':
      handlers.onDelta?.((payload as { text: string }).text)
      break
    case 'done':
      handlers.onDone?.(payload as ChatDone)
      break
    case 'error':
      handlers.onError?.((payload as { message: string }).message)
      break
  }
}
