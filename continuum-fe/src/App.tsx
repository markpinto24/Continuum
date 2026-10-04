import { AudioLines, Inbox, MessageSquare, Network, PanelRightClose, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AccountDialog } from '@/components/account-dialog'
import { AppHeader } from '@/components/app-header'
import { AuthScreen, LEGACY_USER_KEY } from '@/components/auth-screen'
import { BrandMark } from '@/components/brand'
import { BeliefGraph } from '@/components/belief-graph'
import { ChatPanel, type ChatControl } from '@/components/chat-panel'
import { ContradictionInbox } from '@/components/contradiction-inbox'
import { GraphLegend } from '@/components/graph-legend'
import { MemoryDetail } from '@/components/memory-detail'
import { PanelResizer } from '@/components/panel-resizer'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipProvider } from '@/components/ui/tooltip'
import { useResizablePanel } from '@/hooks/use-resizable-panel'
import { usePanelOpen } from '@/hooks/use-panel-open'
import { cn } from '@/lib/utils'
import { useResource } from '@/hooks/use-resource'
import { ApiError, api, onUnauthorized } from '@/lib/api'
import type { GraphResponse, Me, MemoryStatus } from '@/lib/types'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }
/** Gap between the floating sidebar and the window edges, in pixels. */
const PANEL_MARGIN = 12
/** Width of the collapsed rail. */
const RAIL_WIDTH = 48

type Session =
  | { state: 'loading' }
  | { state: 'setup'; webSetupAllowed: boolean }
  | { state: 'signed-out' }
  | { state: 'signed-in'; me: Me }
  | { state: 'unreachable'; message: string }

/**
 * The auth gate. Nothing below it renders — and so nothing fetches memories —
 * until the server has confirmed who is signed in. Signing out unmounts the
 * whole workspace, which is also what clears one person's chat history before
 * the next person signs in on the same browser.
 */
export default function App() {
  const [session, setSession] = useState<Session>({ state: 'loading' })

  const check = useCallback(async () => {
    try {
      const status = await api.authStatus()
      if (status.needs_setup) {
        setSession({ state: 'setup', webSetupAllowed: status.web_setup_allowed })
        return
      }
      setSession({ state: 'signed-in', me: await api.me() })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSession({ state: 'signed-out' })
      } else {
        setSession({ state: 'unreachable', message: err instanceof Error ? err.message : String(err) })
      }
    }
  }, [])

  useEffect(() => {
    void check()
  }, [check])

  // A session that lapses mid-use (expiry, sign-out in another tab, account
  // disabled) lands here from whichever request noticed first.
  useEffect(() => onUnauthorized(() => setSession({ state: 'signed-out' })), [])

  const signedIn = useCallback((me: Me) => {
    try {
      // The pre-auth user id has done its job once an account exists.
      localStorage.removeItem(LEGACY_USER_KEY)
    } catch {
      // Storage can be blocked; nothing depends on this succeeding.
    }
    setSession({ state: 'signed-in', me })
  }, [])

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setSession({ state: 'signed-out' })
    }
  }, [])

  switch (session.state) {
    case 'loading':
      return (
        <Centered>
          <BrandMark className="hud-breathe mx-auto mb-3 size-9 text-accent" />
          Connecting…
        </Centered>
      )
    case 'unreachable':
      return (
        <Centered>
          <BrandMark className="mx-auto mb-4 size-9 text-danger/80" />
          <p className="text-base font-medium text-foreground">Cannot reach the API</p>
          <p className="mt-1.5 text-sm leading-relaxed text-muted">{session.message}</p>
          <p className="mt-4 rounded-lg bg-surface px-3 py-2 text-xs leading-relaxed text-muted">
            From <code className="text-accent">continuum-be/</code> run{' '}
            <code className="text-accent">docker-compose -f local.yml up -d</code>
          </p>
        </Centered>
      )
    case 'setup':
      return (
        <AuthScreen mode="setup" webSetupAllowed={session.webSetupAllowed} onSignedIn={signedIn} />
      )
    case 'signed-out':
      return <AuthScreen mode="login" onSignedIn={signedIn} />
    case 'signed-in':
      return <Workspace key={session.me.user_id} me={session.me} onSignOut={signOut} />
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-8 text-center">
      <div className="max-w-sm animate-fade-up text-sm text-muted">{children}</div>
    </div>
  )
}

function Workspace({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [includeArchived, setIncludeArchived] = useState(false)
  /** A past day, or null: the graph and the chat both look back to it. */
  const [asOfDay, setAsOfDay] = useState<string | null>(null)
  // The end of that day, so everything recorded on it counts.
  const asOf = asOfDay ? new Date(`${asOfDay}T23:59:59`).toISOString() : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState('chat')
  const panel = useResizablePanel()
  const [panelOpen, setPanelOpen] = usePanelOpen()
  /** Open the sidebar on a tab — from the collapsed rail, or a memory link. */
  const openOn = useCallback(
    (next: string) => {
      setTab(next)
      setPanelOpen(true)
    },
    [setPanelOpen],
  )
  const chat = useRef<ChatControl>(null)
  const [lumenAvailable, setLumenAvailable] = useState(false)
  /** Lumen from the rail: full screen over everything, the panel stays put.
   *  If it cannot start (no microphone, say), the chat opens to say why. */
  const askLumen = useCallback(async () => {
    const started = (await chat.current?.startLumen()) ?? false
    if (!started) openOn('chat')
  }, [openOn])
  // What the floating panel covers, so the graph centres on the rest.
  const covered = panelOpen ? panel.width + PANEL_MARGIN * 2 : RAIL_WIDTH + PANEL_MARGIN * 2

  const [accountOpen, setAccountOpen] = useState(false)
  // Loaded once; Settings → Voice refreshes it, and Lumen uses the new voice
  // from her next sentence.
  const voiceSettings = useResource(() => api.voiceSettings(), [])

  const health = useResource(() => api.health(), [])
  const graph = useResource(() => api.graph({ includeArchived, asOf }), [includeArchived, asOf])
  const conflicts = useResource(() => api.conflicts(), [])

  /** Any write to the graph refreshes both views — a resolve changes both. */
  const refreshAll = useCallback(() => {
    graph.refresh()
    conflicts.refresh()
  }, [conflicts, graph])

  const inspect = useCallback(
    (id: string) => {
      setSelectedId(id)
      openOn('memory')
    },
    [openOn],
  )

  const data = graph.data ?? EMPTY_GRAPH
  const counts = useMemo(() => tallyByStatus(data), [data])
  const conflictCount = conflicts.data?.total ?? 0

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-full flex-col overflow-hidden">
        <AppHeader
          me={me}
          onAccount={() => setAccountOpen(true)}
          onSignOut={onSignOut}
          health={health.data}
          nodeCount={data.nodes.length}
          edgeCount={data.edges.length}
          includeArchived={includeArchived}
          onIncludeArchived={setIncludeArchived}
          onRefresh={refreshAll}
          refreshing={graph.loading}
          asOf={asOfDay}
          onAsOf={setAsOfDay}
        />

        {/* The graph fills everything; the sidebar floats over it as glass. */}
        {/* Clipped: the collapsed panel slides off to the right with a
            transform, and anything transformed past the edge still counts as
            page content — without this the whole app scrolls sideways. */}
        <div className="relative min-h-0 flex-1 overflow-hidden">
          <main className="absolute inset-0">
            {graph.error ? (
              <Centered>
                <p className="text-base font-medium text-foreground">Cannot load the graph</p>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{graph.error}</p>
              </Centered>
            ) : (
              <>
                <BeliefGraph
                  data={data}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  rightInset={covered}
                />
                {data.nodes.length > 0 && <GraphLegend counts={counts} />}
                {!graph.loading && data.nodes.length === 0 && (
                  <div
                    className="pointer-events-none absolute inset-y-0 left-0 flex items-center justify-center px-8 text-center"
                    style={{ right: covered }}
                  >
                    <div className="max-w-xs animate-fade-up">
                      <Sparkles className="mx-auto mb-3 size-6 text-accent/70" />
                      <p className="text-base font-medium text-foreground">
                        {asOfDay ? 'Nothing was believed yet' : 'Your graph is empty'}
                      </p>
                      <p className="mt-1.5 text-sm leading-relaxed text-muted">
                        {asOfDay
                          ? 'No memory had been recorded by that day.'
                          : 'Tell Continuum about your work in the chat — decisions, people, constraints. Each fact becomes a node here.'}
                      </p>
                    </div>
                  </div>
                )}
                {asOfDay && (
                  <p
                    className="glass pointer-events-none absolute top-4 -translate-x-1/2 rounded-full px-3.5 py-1.5 text-xs text-amber-200"
                    style={{ left: `calc((100% - ${covered}px) / 2)` }}
                  >
                    Beliefs as of {new Date(`${asOfDay}T12:00:00`).toLocaleDateString()} · confidence
                    is today’s
                  </p>
                )}
              </>
            )}
          </main>

          <aside
            // Hidden, not unmounted: the conversation (and Lumen) carry on
            // while the panel is out of the way.
            inert={!panelOpen}
            aria-hidden={!panelOpen}
            className={cn(
              'liquid-glass absolute flex flex-col rounded-2xl transition-[transform,opacity] duration-300 ease-out',
              !panelOpen && 'pointer-events-none translate-x-[calc(100%+24px)] opacity-0',
            )}
            style={{
              width: panel.width,
              top: PANEL_MARGIN,
              right: PANEL_MARGIN,
              bottom: PANEL_MARGIN,
            }}
          >
            {/* The resize handle rides the panel's left edge. */}
            <div className="absolute inset-y-6 -left-1.5 z-20 flex">
              <PanelResizer
                width={panel.width}
                dragging={panel.dragging}
                {...panel.handleProps}
              />
            </div>
            <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
              <div className="flex items-center gap-1.5 px-3 pt-3 pb-1">
                <TabsList className="glass-inset flex-1 bg-transparent">
                  <TabsTrigger value="chat">
                    <MessageSquare className="size-3.5" />
                    Chat
                  </TabsTrigger>
                  <TabsTrigger value="inbox">
                    <Inbox className="size-3.5" />
                    Inbox
                    {conflictCount > 0 && (
                      <span className="ml-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-400 px-1 text-[10px] font-semibold text-background tabular-nums">
                        {conflictCount}
                      </span>
                    )}
                  </TabsTrigger>
                  <TabsTrigger value="memory">
                    <Network className="size-3.5" />
                    Memory
                  </TabsTrigger>
                </TabsList>
                <Tooltip label={'Hide panel (Ctrl+\\)'}>
                  <button
                    type="button"
                    aria-label="Hide panel"
                    onClick={() => setPanelOpen(false)}
                    className="flex size-9 shrink-0 items-center justify-center rounded-xl text-muted transition-colors hover:bg-white/5 hover:text-foreground"
                  >
                    <PanelRightClose className="size-4" />
                  </button>
                </Tooltip>
              </div>

              <TabsContent value="chat" forceMount className="min-h-0">
                <ChatPanel
                  controlRef={chat}
                  onLumenAvailable={setLumenAvailable}
                  me={me}
                  asOf={asOf}
                  voiceSettings={voiceSettings.data}
                  openDisputes={conflicts.data?.total ?? null}
                  onGraphChanged={refreshAll}
                  onSelectMemory={inspect}
                />
              </TabsContent>

              <TabsContent value="inbox" className="min-h-0">
                <ContradictionInbox
                  resource={conflicts}
                  onResolved={graph.refresh}
                  onSelect={inspect}
                  isAdmin={me.is_admin}
                />
              </TabsContent>

              <TabsContent value="memory" className="min-h-0">
                <MemoryDetail
                  memoryId={selectedId}
                  onSelect={setSelectedId}
                  onMutated={refreshAll}
                  userId={me.user_id}
                  isAdmin={me.is_admin}
                />
              </TabsContent>
            </Tabs>
          </aside>

          {!panelOpen && (
            <nav
              aria-label="Open the panel"
              className="liquid-glass absolute top-1/2 flex -translate-y-1/2 flex-col items-center gap-1 rounded-2xl p-1.5 animate-fade-up"
              style={{ right: PANEL_MARGIN, width: RAIL_WIDTH }}
            >
              <RailButton label="Open chat" onClick={() => openOn('chat')}>
                <MessageSquare className="size-4" />
              </RailButton>
              <RailButton label="Open inbox" onClick={() => openOn('inbox')} badge={conflictCount}>
                <Inbox className="size-4" />
              </RailButton>
              <RailButton label="Open memory" onClick={() => openOn('memory')}>
                <Network className="size-4" />
              </RailButton>
              {lumenAvailable && (
                <>
                  <span className="my-1 h-px w-6 bg-white/10" />
                  <Tooltip label="Ask Lumen — talk hands-free" side="left">
                    <button
                      type="button"
                      aria-label="Ask Lumen"
                      onClick={() => void askLumen()}
                      className="group relative flex size-9 items-center justify-center rounded-xl text-accent ring-1 ring-accent/35 transition-all hover:bg-accent/15 hover:ring-accent/70 hover:shadow-[0_0_18px_-4px_var(--color-accent)]"
                    >
                      <span className="hud-breathe absolute inset-0 rounded-xl bg-accent/10" />
                      <AudioLines className="relative size-4" />
                    </button>
                  </Tooltip>
                </>
              )}
            </nav>
          )}
        </div>

        <AccountDialog
          me={me}
          open={accountOpen}
          onOpenChange={setAccountOpen}
          voiceSettings={voiceSettings}
        />
      </div>
    </TooltipProvider>
  )
}

function tallyByStatus(graph: GraphResponse): Record<MemoryStatus, number> {
  const counts: Record<MemoryStatus, number> = {
    active: 0,
    contradicted: 0,
    superseded: 0,
    archived: 0,
  }
  for (const node of graph.nodes) counts[node.status] += 1
  return counts
}

function RailButton({
  label,
  onClick,
  badge = 0,
  children,
}: {
  label: string
  onClick: () => void
  badge?: number
  children: React.ReactNode
}) {
  return (
    <Tooltip label={`${label} (Ctrl+\\)`} side="left">
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className="relative flex size-9 items-center justify-center rounded-xl text-muted transition-colors hover:bg-white/10 hover:text-foreground"
      >
        {children}
        {badge > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-400 px-1 text-[10px] font-semibold text-background tabular-nums">
            {badge}
          </span>
        )}
      </button>
    </Tooltip>
  )
}
