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

export interface Escalation {
  target_id: string
  judge_relation: 'supersedes' | 'conflict' | null
  judge_confidence: number | null
  similarity: number | null
  gate: number
  forced: boolean
  reason: string
  raised_at: string
}

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
  /** On a private memory: team memories it disagrees with. Only this side carries the edge. */
  team_conflicts_with: string[]
  /** What the resolver thought each time it escalated this memory to a person. */
  escalations: Escalation[]
  /** Set on memories in the shared team space: who put it there. */
  shared_by: string | null
  shared_by_email: string | null
  /** On a private memory that was shared: the shared memory that now carries it. */
  shared_as: string | null
  /** A summary is derived from other memories, never evidence of its own. */
  kind: MemoryKind
  derived_from: string[]
  /** Set when a person said "this isn't a real fact". */
  rejected_reason: RejectReason | null
  /** Set when a person forgot it: content, excerpt and subject are gone. */
  redacted_at: string | null
  superseded_at: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
  last_reinforced_at: string
  reinforcement_count: number
}

export type MemoryKind = 'fact' | 'summary'

// --- Graph -----------------------------------------------------------------

export interface GraphNode {
  id: string
  label: string
  category: MemoryCategory
  status: MemoryStatus
  confidence: number
  subject: string | null
  created_at: string
  /** In the shared team space rather than your own graph. */
  shared: boolean
  shared_by_email: string | null
  kind: MemoryKind
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
  /** Your private belief against the team's — settled with resolveTeamConflict. */
  team: boolean
}

export type TeamDecision = 'team_holds' | 'mine_holds' | 'both_hold'

export interface TeamResolutionResponse {
  memory: Memory
  action: TeamDecision
  shared: ShareResponse | null
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
  /** Team memories a new private one disagrees with. */
  team_conflicts: string[]
  resolutions: ResolutionRecord[]
}

// --- Chat ------------------------------------------------------------------

export interface RetrievedMemory {
  memory: Memory
  /** Raw cosine score. */
  similarity: number
  /** Topicality weight, 1.0 = reinforced just now. */
  recency: number
  /** Share of the question's content words this memory contains. */
  keyword: number
  /** max(similarity, keyword x w) x confidence^w x recency^w — the rank actually used. */
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
  /** Set when these are the beliefs held at a past moment. */
  as_of: string | null
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
  /** The server speaks answers with a local voice; otherwise the browser's is used. */
  synthesis: boolean
  synthesis_ready: boolean
  synthesis_max_chars: number
}

export interface Transcription {
  text: string
  language: string
  duration_seconds: number
}

// --- Learning from decisions -------------------------------------------------

export interface GateBand {
  low: number
  high: number
  total: number
  confirmed: number
  refuted: number
}

/** Would a lower auto-supersede gate have been safe? Counted from real decisions. */
export interface GateEvidence {
  gate: number
  labels: number
  with_judgement: number
  bands: GateBand[]
  below_gate_total: number
  below_gate_refuted: number
  error_upper_bound: number | null
  role_rule_total: number
  role_rule_shared: number
  conflicts_total: number
  conflicts_both_hold: number
  recommendation: string
}

// --- Shared team space -------------------------------------------------------

/** The owner id of the shared team space — never a real account. */
export const SHARED_SPACE = '_shared'

export interface ShareResponse {
  /** created: new to the team · merged: already known · superseded: replaced an
   *  older shared belief · conflict: contradicts one, now in the shared inbox. */
  outcome: 'created' | 'merged' | 'superseded' | 'conflict'
  original: Memory
  shared: Memory
}

// --- Voice ---------------------------------------------------------------------

export interface VoiceOption {
  id: string
  label: string
  accent: string
  gender: string
}

/** How Lumen and read-aloud sound for you. Saved to your account. */
export interface VoiceSettings {
  voice: string
  /** 1 = natural pace. */
  speed: number
  default_voice: string
  voices: VoiceOption[]
}

// --- Teaching it -----------------------------------------------------------------

export type RejectReason = 'not_a_fact' | 'merged' | 'misread' | 'other'

export interface RejectResponse {
  memory: Memory
  /** Beliefs the misreading had retired, now active again. */
  restored: string[]
}

export interface CalibrationPoint {
  low: number
  high: number
  total: number
  confirmed: number
  observed: number | null
  calibrated: number
}

export interface CalibrationReport {
  labels: number
  points: CalibrationPoint[]
  usable: boolean
  in_use: boolean
  min_labels: number
}

export interface ResolutionRule {
  id: string
  owner: string
  subject: string
  kind: string
  created_by: string
  created_at: string
  revoked_at: string | null
}

export interface RuleSuggestion {
  owner: string
  subject: string
  both_hold: number
}

export interface RuleList {
  rules: ResolutionRule[]
  suggestions: RuleSuggestion[]
}

export interface FeedbackSummary {
  decisions: number
  rejected_facts: number
  answers_up: number
  answers_down: number
  missing_memories: number
}

export interface AnswerRating {
  query: string
  answer: string
  /** +1 helpful, -1 not, 0 only reporting a missing memory. */
  rating: -1 | 0 | 1
  note?: string | null
  used_ids?: string[]
  cited_ids?: string[]
  missing_ids?: string[]
}

export interface VoiceChoice {
  voice?: string
  speed?: number
}
