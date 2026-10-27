/**
 * Thin typed client over the Continuum API.
 *
 * Same principle the backend applies to Qdrant: own the ~100 lines rather than
 * take a data-fetching framework's abstraction. Nothing here makes a policy
 * decision — it builds a URL, checks the status, and returns a typed payload.
 */

import type {
  ActivityReport,
  ApiKeyCreated,
  ApiKeySummary,
  AuthStatus,
  ChatContext,
  ChatDone,
  ChatMessage,
  ChatRemembered,
  Mode,
  ConflictListResponse,
  ConflictResolutionRequest,
  ConflictResolutionResponse,
  GateEvidence,
  GraphResponse,
  HealthResponse,
  IngestResponse,
  Me,
  Memory,
  MemoryListResponse,
  AnswerRating,
  CalibrationReport,
  FeedbackSummary,
  RejectReason,
  RejectResponse,
  ResolutionRule,
  RuleList,
  TeamDecision,
  TeamResolutionResponse,
  ShareResponse,
  SpeechStatus,
  Transcription,
  UserSummary,
  VoiceChoice,
  VoiceSettings,
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

/**
 * The graph the UI is showing — Personal or Workspace — named on every request.
 * It only picks between the signed-in person's OWN graphs; who they are still
 * comes from the session cookie. Null until the account says where it opens.
 */
let currentMode: Mode | null = null

export function setMode(mode: Mode | null): void {
  currentMode = mode
}

export function getMode(): Mode | null {
  return currentMode
}

function modeHeader(): Record<string, string> {
  return currentMode ? { 'x-continuum-mode': currentMode } : {}
}

/** A download link cannot carry a header, so it names the mode in its query. */
function withMode(url: string, params: Record<string, string> = {}): string {
  const query = new URLSearchParams({ ...params, ...(currentMode ? { mode: currentMode } : {}) })
  const text = query.toString()
  return text ? `${url}?${text}` : url
}

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
  return {
    'content-type': 'application/json',
    ...CLIENT_HEADER,
    ...modeHeader(),
    ...init?.headers,
  }
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

  graph: (opts: { limit?: number; includeArchived?: boolean; asOf?: string | null } = {}) =>
    request<GraphResponse>(
      `/memories/graph?${new URLSearchParams({
        // The API's ceiling. Grouping by subject keeps this many drawable; a
        // graph cut below it says so (`truncated`).
        limit: String(opts.limit ?? 2000),
        include_archived: String(opts.includeArchived ?? false),
        ...(opts.asOf ? { as_of: opts.asOf } : {}),
      })}`,
    ),

  memory: (id: string) => request<Memory>(`/memories/${id}`),

  memories: (limit = 500) =>
    request<MemoryListResponse>(`/memories?${new URLSearchParams({ limit: String(limit) })}`),

  /** Confirm a memory is still true. Raises confidence, resets the decay clock. */
  reinforce: (id: string) => request<Memory>(`/memories/${id}/reinforce`, post()),

  /** Put one of your memories in the shared team space. */
  shareMemory: (id: string) => request<ShareResponse>(`/memories/${id}/share`, post()),

  // --- Pending: said, not decided ----------------------------------------------
  pendingMemories: (limit = 100) =>
    request<MemoryListResponse>(
      `/memories?${new URLSearchParams({ status: 'pending', limit: String(limit) })}`,
    ),
  /** Yes: it becomes a belief, judged against the graph now. */
  confirmPending: (id: string) => request<IngestResponse>(`/memories/${id}/confirm`, post()),
  /** No: hidden for good, kept for the record. */
  declinePending: (id: string) => request<Memory>(`/memories/${id}/decline`, post()),
  /** Still unsure: stays pending, counts as one reminder. */
  unsurePending: (id: string) => request<Memory>(`/memories/${id}/unsure`, post()),

  /** Bring an archived or superseded memory back. Nothing here is a one-way door. */
  reactivate: (id: string) => request<Memory>(`/memories/${id}/reactivate`, post()),

  /** "This isn't a real fact": archived with the reason, and anything it retired comes back. */
  rejectMemory: (id: string, reason: RejectReason, note?: string) =>
    request<RejectResponse>(`/memories/${id}/reject`, post({ reason, note: note || null })),

  /** Erase what a memory says, everywhere it was copied. The one thing that cannot be undone. */
  forgetMemory: (id: string) => request<Memory>(`/memories/${id}/forget`, post()),

  /** A plain link: this mode's whole graph as JSON, downloaded with the session cookie. */
  memoryExportUrl: () => withMode(`${PREFIX}/memories/export`),

  refreshSummaries: () => request<{ written: Memory[] }>('/memories/summaries/refresh', post()),

  searchMemories: (query: string, limit = 8) =>
    request<{ query: string; results: { memory: Memory; score: number }[] }>(
      '/memories/search',
      post({ query, limit }),
    ),

  conflicts: (limit = 100) =>
    request<ConflictListResponse>(`/conflicts?${new URLSearchParams({ limit: String(limit) })}`),

  resolveConflict: (body: ConflictResolutionRequest) =>
    request<ConflictResolutionResponse>('/conflicts/resolve', post(body)),

  resolveTeamConflict: (memoryId: string, sharedId: string, decision: TeamDecision) =>
    request<TeamResolutionResponse>(
      '/conflicts/resolve-team',
      post({ memory_id: memoryId, shared_id: sharedId, decision }),
    ),

  ingest: (text: string) => request<IngestResponse>('/ingest', post({ text })),

  chatContext: (query: string, asOf?: string | null) =>
    request<ChatContext>('/chat/context', post({ query, as_of: asOf || null })),

  // --- Authentication -------------------------------------------------------

  authStatus: () => request<AuthStatus>('/auth/status'),
  me: () => request<Me>('/auth/me'),
  login: (email: string, password: string) =>
    request<Me>('/auth/login', post({ email, password })),
  /** First admin. `userId` adopts memories stored before sign-in existed. */
  setup: (email: string, password: string, userId?: string, defaultMode?: Mode) =>
    request<Me>(
      '/auth/setup',
      post({ email, password, user_id: userId || null, default_mode: defaultMode ?? null }),
    ),
  /** Where the account opens. A person's choice: an API key cannot change it. */
  setDefaultMode: (mode: Mode) =>
    request<Me>('/auth/mode', { method: 'PATCH', body: JSON.stringify({ default_mode: mode }) }),
  logout: () => request<void>('/auth/logout', post()),
  voiceSettings: () => request<VoiceSettings>('/auth/preferences'),
  saveVoiceSettings: (choice: { voice?: string | null; speed?: number | null }) =>
    request<VoiceSettings>('/auth/preferences', {
      method: 'PATCH',
      body: JSON.stringify(choice),
    }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>(
      '/auth/password',
      post({ current_password: currentPassword, new_password: newPassword }),
    ),

  apiKeys: () => request<{ keys: ApiKeySummary[] }>('/auth/keys'),
  createApiKey: (name: string, mode: Mode = 'workspace') =>
    request<ApiKeyCreated>('/auth/keys', post({ name, mode })),
  revokeApiKey: (id: string) => request<void>(`/auth/keys/${id}`, { method: 'DELETE' }),

  users: () => request<{ users: UserSummary[] }>('/admin/users'),
  createUser: (body: { email: string; password: string; is_admin: boolean; user_id?: string }) =>
    request<UserSummary>('/admin/users', post({ ...body, user_id: body.user_id || null })),
  updateUser: (id: string, body: { disabled?: boolean; is_admin?: boolean }) =>
    request<UserSummary>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  // --- Learning from decisions ---------------------------------------------

  feedbackEvidence: (scope: 'mine' | 'team' = 'mine') =>
    request<GateEvidence>(`/feedback/evidence?scope=${scope}`),
  /**
   * A plain link: the browser downloads it with the session cookie. Health
   * decisions stay out unless `sensitive` — an export is a file that travels.
   */
  feedbackExportUrl: (opts: { sensitive?: boolean } = {}) =>
    withMode(`${PREFIX}/feedback/export`, opts.sensitive ? { include_sensitive: 'true' } : {}),
  feedbackExportUrlFor: (
    kind: 'resolution' | 'extraction' | 'retrieval',
    opts: { sensitive?: boolean } = {},
  ) =>
    withMode(`${PREFIX}/feedback/export`, {
      kind,
      ...(opts.sensitive ? { include_sensitive: 'true' } : {}),
    }),
  calibration: () => request<CalibrationReport>('/feedback/calibration'),
  rules: () => request<RuleList>('/feedback/rules'),
  approveRule: (subject: string, scope: 'mine' | 'team' = 'mine') =>
    request<ResolutionRule>('/feedback/rules', post({ subject, scope })),
  revokeRule: (id: string) => request<void>(`/feedback/rules/${id}`, { method: 'DELETE' }),
  rateAnswer: (body: AnswerRating) => request<{ missing: number }>('/feedback/answer', post(body)),
  feedbackSummary: () => request<FeedbackSummary>('/feedback/summary'),
  /** The resolver over time, in the current graph. */
  feedbackActivity: (days: number) => request<ActivityReport>(`/feedback/activity?days=${days}`),

  // --- Dictation ------------------------------------------------------------

  speechStatus: () => request<SpeechStatus>('/speech/status'),

  /** One chunk of an answer, spoken by the server's local voice, as WAV. */
  synthesize: async (text: string, choice: VoiceChoice = {}): Promise<Blob> => {
    const response = await fetch(`${PREFIX}/speech/synthesize`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers(),
      body: JSON.stringify({ text, ...choice }),
    })
    if (!response.ok) return fail('/speech/synthesize', response)
    return response.blob()
  },

  /** Upload one recording; the text comes back. Nothing is stored server-side. */
  transcribe: async (clip: Blob): Promise<Transcription> => {
    const form = new FormData()
    const extension = clip.type.includes('ogg') ? 'ogg' : clip.type.includes('mp4') ? 'mp4' : 'webm'
    form.append('audio', clip, `dictation.${extension}`)
    const response = await fetch(`${PREFIX}/speech/transcribe`, {
      method: 'POST',
      credentials: 'same-origin',
      // No content-type: the browser sets multipart/form-data with its boundary.
      headers: { ...CLIENT_HEADER, ...modeHeader() },
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
  /** The answer is complete. Memory may still be being written. */
  onDone?: (done: ChatDone) => void
  /** The turn landed in memory (only when `done.remembering` was set). */
  onRemembered?: (remembered: ChatRemembered) => void
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
  body: {
    messages: ChatMessage[]
    remember?: boolean
    limit?: number
    share?: boolean
    as_of?: string | null
  },
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
    case 'remembered':
      handlers.onRemembered?.(payload as ChatRemembered)
      break
    case 'error':
      handlers.onError?.((payload as { message: string }).message)
      break
  }
}
