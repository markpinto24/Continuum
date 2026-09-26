/**
 * The backend contract, mirrored.
 *
 * Hand-written rather than generated from OpenAPI: the surface is small, it
 * changes rarely, and a hand-written mirror is the one place where a breaking
 * backend change shows up as a type error instead of a runtime `undefined`.
 * If you change `models/schemas.py`, change this file in the same commit.
 */

export type MemoryCategory =
  | 'decision'
  | 'preference'
  | 'fact'
  | 'event'
  | 'person'
  | 'constraint'

export type MemoryStatus = 'active' | 'superseded' | 'contradicted' | 'archived'

export interface Memory {
  id: string
  user_id: string
  content: string
  category: MemoryCategory
  subject: string | null
  confidence: number
  status: MemoryStatus
  source_id: string | null
  source_excerpt: string | null
  /** Belief-graph edges. Never a delete — this is what replaced what. */
  supersedes: string[]
  superseded_by: string | null
  conflicts_with: string[]
  created_at: string
  updated_at: string
  last_reinforced_at: string
  reinforcement_count: number
}

// --- Graph -----------------------------------------------------------------

export interface GraphNode {
  id: string
  label: string
  category: MemoryCategory
  status: MemoryStatus
  confidence: number
  subject: string | null
  created_at: string
}

export type GraphEdgeKind = 'supersedes' | 'conflicts_with'

export interface GraphEdge {
  source: string
  target: string
  kind: GraphEdgeKind
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

// --- Conflicts -------------------------------------------------------------

export interface ConflictPair {
  memory: Memory
  conflicting: Memory[]
}

export interface ConflictListResponse {
  total: number
  conflicts: ConflictPair[]
}

export interface ConflictResolutionRequest {
  winner_id: string
  loser_ids: string[]
  keep_both: boolean
}

export interface ConflictResolutionResponse {
  winner: Memory
  losers: Memory[]
  action: 'superseded' | 'kept_both'
}

// --- Ingest ----------------------------------------------------------------

export interface ResolutionRecord {
  memory_id: string
  content: string
  verdict: string
  target_id: string | null
  judge_confidence: number | null
  reason: string
  escalated: boolean
}

export interface IngestResponse {
  source_id: string
  extracted: number
  created: Memory[]
  duplicates_skipped: number
  reinforced: string[]
  superseded: string[]
  conflicts_raised: string[]
  resolutions: ResolutionRecord[]
}

// --- Chat ------------------------------------------------------------------

export interface RetrievedMemory {
  memory: Memory
  /** Raw cosine score. */
  similarity: number
  /** Topicality weight, 1.0 = reinforced just now. */
  recency: number
  /** similarity x confidence^w x recency^w — the rank actually used. */
  score: number
}

export interface Disagreement {
  subject: string | null
  memories: Memory[]
}

export interface ChatContext {
  query: string
  memories: RetrievedMemory[]
  disagreements: Disagreement[]
}

export interface ChatDone {
  memory_ids: string[]
  cited_ids: string[]
  disagreements: number
  remembered: IngestResponse | null
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

// --- Misc ------------------------------------------------------------------

export interface HealthResponse {
  status: 'ok' | 'degraded'
  app: string
  environment: string
  qdrant: string
  llm: string
  database: string
}

export interface MemoryListResponse {
  total: number
  memories: Memory[]
}

// --- Authentication ----------------------------------------------------------
// The memory owner is whoever the session or API key belongs to. No request
// body carries a user_id any more; the server refuses one if sent.

export interface AuthStatus {
  needs_setup: boolean
  web_setup_allowed: boolean
}

export interface Me {
  user_id: string
  email: string
  is_admin: boolean
  via: 'session' | 'api_key'
}

export interface ApiKeySummary {
  id: string
  name: string
  /** The first characters of the key — enough to recognise it, not to use it. */
  prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export interface ApiKeyCreated {
  key: ApiKeySummary
  /** The full key. Returned once, by the create call, and never again. */
  secret: string
}

export interface UserSummary {
  id: string
  email: string
  is_admin: boolean
  disabled: boolean
  created_at: string
}

// --- Dictation ---------------------------------------------------------------

export interface SpeechStatus {
  enabled: boolean
  /** Model loaded. False during the server's first download — dictation still works, slower. */
  ready: boolean
  max_seconds: number
  language: string | null
}

export interface Transcription {
  text: string
  language: string
  duration_seconds: number
}
