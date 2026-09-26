/**
 * Thin typed client over the Continuum API.
 *
 * Same principle the backend applies to Qdrant: own the ~100 lines rather than
 * take a data-fetching framework's abstraction. Nothing here makes a policy
 * decision — it builds a URL, checks the status, and returns a typed payload.
 */

import type {
  ApiKeyCreated,
  ApiKeySummary,
  AuthStatus,
  ChatContext,
  ChatDone,
  ChatMessage,
  ConflictListResponse,
  ConflictResolutionRequest,
  ConflictResolutionResponse,
  GraphResponse,
  HealthResponse,
  IngestResponse,
  Me,
  Memory,
  MemoryListResponse,
  SpeechStatus,
  Transcription,
  UserSummary,
} from './types'
import { createSSEParser } from './sse'

/** Empty by default: the Vite dev server proxies /api to the backend. */
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

/**
 * Sent on every request. The server requires it on any cookie-authenticated
 * write: a custom header cannot be attached cross-site without a CORS preflight,
 * so a forged form post from another site is refused even if it somehow carried
 * the session cookie.
 */
const CLIENT_HEADER = { 'x-continuum-client': 'web' }

// Wrong credentials on these are an answer for the form, not a lapsed session.
const SIGN_IN_PATHS = new Set(['/auth/login', '/auth/setup', '/auth/password'])

let unauthorizedListener: (() => void) | null = null

/**
 * Called whenever the server says the session is gone — expired, signed out in
 * another tab, or the account was disabled — so the app can show sign-in instead
 * of a panel full of 401 errors. Returns an unsubscribe function.
 */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListener = listener
  return () => {
    if (unauthorizedListener === listener) unauthorizedListener = null
  }
}

function headers(init?: RequestInit): HeadersInit {
  return { 'content-type': 'application/json', ...CLIENT_HEADER, ...init?.headers }
}

async function fail(path: string, response: Response): Promise<never> {
  if (response.status === 401 && !SIGN_IN_PATHS.has(path)) unauthorizedListener?.()
  throw new ApiError(await describeFailure(response), response.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${PREFIX}${path}`, {
    ...init,
    // The session cookie is httpOnly: the page never sees it, the browser just
    // sends it to its own origin.
    credentials: 'same-origin',
    headers: headers(init),
  })

  if (!response.ok) return fail(path, response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})

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

  graph: (opts: { limit?: number; includeArchived?: boolean } = {}) =>
    request<GraphResponse>(
      `/memories/graph?${new URLSearchParams({
        limit: String(opts.limit ?? 500),
        include_archived: String(opts.includeArchived ?? false),
      })}`,
    ),

  memory: (id: string) => request<Memory>(`/memories/${id}`),

  memories: (limit = 500) =>
    request<MemoryListResponse>(`/memories?${new URLSearchParams({ limit: String(limit) })}`),

  /** Confirm a memory is still true. Raises confidence, resets the decay clock. */
  reinforce: (id: string) => request<Memory>(`/memories/${id}/reinforce`, post()),

  /** Bring an archived or superseded memory back. Nothing here is a one-way door. */
  reactivate: (id: string) => request<Memory>(`/memories/${id}/reactivate`, post()),

  conflicts: (limit = 100) =>
    request<ConflictListResponse>(`/conflicts?${new URLSearchParams({ limit: String(limit) })}`),

  resolveConflict: (body: ConflictResolutionRequest) =>
    request<ConflictResolutionResponse>('/conflicts/resolve', post(body)),

  ingest: (text: string) => request<IngestResponse>('/ingest', post({ text })),

  chatContext: (query: string) => request<ChatContext>('/chat/context', post({ query })),

  // --- Authentication -------------------------------------------------------

  authStatus: () => request<AuthStatus>('/auth/status'),
  me: () => request<Me>('/auth/me'),
  login: (email: string, password: string) =>
    request<Me>('/auth/login', post({ email, password })),
  /** First admin. `userId` adopts memories stored before sign-in existed. */
  setup: (email: string, password: string, userId?: string) =>
    request<Me>('/auth/setup', post({ email, password, user_id: userId || null })),
  logout: () => request<void>('/auth/logout', post()),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>(
      '/auth/password',
      post({ current_password: currentPassword, new_password: newPassword }),
    ),

  apiKeys: () => request<{ keys: ApiKeySummary[] }>('/auth/keys'),
  createApiKey: (name: string) => request<ApiKeyCreated>('/auth/keys', post({ name })),
  revokeApiKey: (id: string) => request<void>(`/auth/keys/${id}`, { method: 'DELETE' }),

  users: () => request<{ users: UserSummary[] }>('/admin/users'),
  createUser: (body: { email: string; password: string; is_admin: boolean; user_id?: string }) =>
    request<UserSummary>('/admin/users', post({ ...body, user_id: body.user_id || null })),
  updateUser: (id: string, body: { disabled?: boolean; is_admin?: boolean }) =>
    request<UserSummary>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  // --- Dictation ------------------------------------------------------------

  speechStatus: () => request<SpeechStatus>('/speech/status'),

  /** Upload one recording; the text comes back. Nothing is stored server-side. */
  transcribe: async (clip: Blob): Promise<Transcription> => {
    const form = new FormData()
    const extension = clip.type.includes('ogg') ? 'ogg' : clip.type.includes('mp4') ? 'mp4' : 'webm'
    form.append('audio', clip, `dictation.${extension}`)
    const response = await fetch(`${PREFIX}/speech/transcribe`, {
      method: 'POST',
      credentials: 'same-origin',
      // No content-type: the browser sets multipart/form-data with its boundary.
      headers: CLIENT_HEADER,
      body: form,
    })
    if (!response.ok) return fail('/speech/transcribe', response)
    return (await response.json()) as Transcription
  },
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
  body: { messages: ChatMessage[]; remember?: boolean; limit?: number },
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${PREFIX}/chat`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) return fail('/chat', response)
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
