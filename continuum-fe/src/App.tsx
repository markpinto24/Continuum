import { Heart, Inbox, MessageSquare, Network, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AccountDialog } from '@/components/account-dialog'
import { AppBackdrop } from '@/components/app-backdrop'
import { AppDock, COLUMNS, type Column, type Columns } from '@/components/app-dock'
import { AppHeader } from '@/components/app-header'
import { AuthScreen, LEGACY_USER_KEY } from '@/components/auth-screen'
import { BrandMark } from '@/components/brand'
import { BeliefGraph } from '@/components/belief-graph'
import { ChatPanel, type ChatControl } from '@/components/chat-panel'
import { ContradictionInbox } from '@/components/contradiction-inbox'
import { GraphList } from '@/components/graph-list'
import { type Grouping, GraphToolbar } from '@/components/graph-toolbar'
import { MemoryDetail } from '@/components/memory-detail'
import { PanelResizer } from '@/components/panel-resizer'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipProvider } from '@/components/ui/tooltip'
import { NARROW, useMediaQuery } from '@/hooks/use-media-query'
import { useResizablePanel } from '@/hooks/use-resizable-panel'
import { PANEL_MIN } from '@/lib/panel-width'
import { cn } from '@/lib/utils'
import { useMode } from '@/hooks/use-mode'
import { useResource } from '@/hooks/use-resource'
import { ApiError, api, onUnauthorized, setMode } from '@/lib/api'
import {
  AUTO_CLUSTER_ABOVE,
  CLUSTER_PREFIX,
  clusterBySubject,
  filterGraph,
  isCluster,
} from '@/lib/graph-view'
import type { GraphResponse, Me, MemoryCategory, MemoryStatus, Mode } from '@/lib/types'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }
/** Lumen's column beside the graph. Alone, or beside only the chat, it is the wide one. */
const LUMEN_WIDTH = 420
/** The dock's strip on the left, and the least the graph keeps beside Lumen and the chat. */
const DOCK_WIDTH = 108
const GRAPH_MIN = 300

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
  // Signed out, there is no mode until the next account says where it opens.
  // Cleared here, not on unmount: StrictMode's rehearsal unmount would clear it
  // between the first render and the requests that need it.
  const toSignedOut = useCallback(() => {
    setMode(null)
    setSession({ state: 'signed-out' })
  }, [])

  useEffect(() => onUnauthorized(toSignedOut), [toSignedOut])

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
      toSignedOut()
    }
  }, [toSignedOut])

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
      return <Signedin key={session.me.user_id} me={session.me} onSignOut={signOut} />
  }
}

/**
 * One workspace per mode, keyed on it: switching unmounts the other mode's
 * graph, chat and Lumen entirely, so no conversation, selection or request can
 * carry across (requirements.md §7).
 */
function Signedin({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [mode, setMode] = useMode(me)
  return <Workspace key={mode} me={me} mode={mode} onMode={setMode} onSignOut={onSignOut} />
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-8 text-center">
      <div className="max-w-sm animate-fade-up text-sm text-muted">{children}</div>
    </div>
  )
}

function Workspace({
  me,
  mode,
  onMode,
  onSignOut,
}: {
  me: Me
  mode: Mode
  onMode: (mode: Mode) => void
  onSignOut: () => void
}) {
  const [includeArchived, setIncludeArchived] = useState(false)
  /** A past day, or null: the graph and the chat both look back to it. */
  const [asOfDay, setAsOfDay] = useState<string | null>(null)
  // The end of that day, so everything recorded on it counts.
  const asOf = asOfDay ? new Date(`${asOfDay}T23:59:59`).toISOString() : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // What the graph shows (lib/graph-view). Statuses are hidden from the toolbar.
  const [hiddenStatuses, setHiddenStatuses] = useState<ReadonlySet<MemoryStatus>>(new Set())
  const [category, setCategory] = useState<MemoryCategory | 'all'>('all')
  const [sinceDays, setSinceDays] = useState<number | null>(null)
  const [grouping, setGrouping] = useState<Grouping>('auto')
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())

  // What is on screen. Every session opens on Lumen; the dock adds or removes
  // the graph and the chat beside it, always in the order memories | Lumen |
  // chat. On a phone there is room for one at a time.
  const narrow = useMediaQuery(NARROW)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const [shown, setShown] = useState<Columns>({ memories: false, lumen: true, chat: false })
  const columns: Columns = narrow ? onlyOne(shown) : shown
  const toggle = useCallback(
    (column: Column) =>
      setShown((was) => {
        if (narrow) return { memories: false, lumen: false, chat: false, [column]: true }
        const next = { ...was, [column]: !was[column] }
        // Something is always on screen: the last one open stays open.
        return next.memories || next.lumen || next.chat ? next : was
      }),
    [narrow],
  )
  const open = useCallback(
    (column: Column) =>
      setShown((was) => (narrow ? { memories: false, lumen: false, chat: false, [column]: true } : { ...was, [column]: true })),
    [narrow],
  )
  // The inbox, or one memory's detail, over whatever is showing.
  const [sheet, setSheet] = useState<'inbox' | 'memory' | null>(null)

  // Ctrl+\ (Cmd+\ on a Mac): the chat, in or out.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSheet(null)
      if (event.key !== '\\' || !(event.ctrlKey || event.metaKey)) return
      event.preventDefault()
      toggle('chat')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle])

  const panel = useResizablePanel()
  const lumenPanel = useResizablePanel('continuum.lumen_width', LUMEN_WIDTH)
  const chat = useRef<ChatControl>(null)
  const [lumenAvailable, setLumenAvailable] = useState(true)
  const [lumenActive, setLumenActive] = useState(false)
  // Lumen is drawn into its column by the chat, which owns the conversation and
  // the voice session; with Lumen closed it is drawn nowhere.
  const [lumenHost, setLumenHost] = useState<HTMLDivElement | null>(null)
  /** "Let Lumen take over": Lumen in the chat's place, started by this very
   *  click — a browser lets the microphone and the voice start only from one. */
  const takeOver = useCallback(() => {
    setShown((was) =>
      narrow ? { memories: false, lumen: true, chat: false } : { ...was, lumen: true, chat: false },
    )
    void chat.current?.startLumen()
  }, [narrow])
  // Lumen is the wide column unless the graph is beside it.
  const lumenWide = !columns.memories
  // Beside the graph, Lumen never squeezes it below a usable width.
  const lumenWidth = Math.min(
    lumenPanel.width,
    Math.max(PANEL_MIN, viewport - DOCK_WIDTH - (columns.chat ? panel.width : 0) - GRAPH_MIN),
  )
  // Beside the graph or Lumen, the chat is a resizable column; alone, a readable one.
  const chatBeside = !narrow && (columns.memories || columns.lumen)

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

  /** A memory's detail, from the graph or a citation in the chat. */
  const inspect = useCallback((id: string) => {
    setSelectedId(id)
    setSheet('memory')
  }, [])

  const data = graph.data ?? EMPTY_GRAPH
  const counts = useMemo(() => tallyByStatus(data), [data])
  const filtered = useMemo(
    () =>
      filterGraph(data, {
        hiddenStatuses,
        categories: new Set(category === 'all' ? [] : [category]),
        sinceDays,
      }),
    [data, hiddenStatuses, category, sinceDays],
  )
  const grouped =
    grouping === 'on' || (grouping === 'auto' && filtered.nodes.length > AUTO_CLUSTER_ABOVE)
  const view = useMemo(
    () => (grouped ? clusterBySubject(filtered, expanded) : filtered),
    [grouped, filtered, expanded],
  )
  const presentCategories = useMemo(
    () => [...new Set(data.nodes.map((n) => n.category))].sort() as MemoryCategory[],
    [data],
  )
  /** A cluster opens into its memories; a memory opens its detail. */
  const selectNode = useCallback(
    (id: string | null) => {
      if (id && isCluster(id)) {
        const subject = id.slice(CLUSTER_PREFIX.length)
        setExpanded((was) => new Set([...was, subject]))
        return
      }
      if (id) inspect(id)
      else setSelectedId(null)
    },
    [inspect],
  )
  const toggleStatus = useCallback((status: MemoryStatus) => {
    setHiddenStatuses((was) => {
      const next = new Set(was)
      if (!next.delete(status)) next.add(status)
      return next
    })
  }, [])
  const conflictCount = conflicts.data?.total ?? 0

  return (
    <TooltipProvider delayDuration={200}>
      {/* isolate: the backdrop's -z-10 stays behind this, not behind the page. */}
      <div className="relative isolate flex h-full flex-col overflow-hidden">
        <AppBackdrop />
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
          mode={mode}
          onMode={onMode}
          graphControls={columns.memories}
        />

        <div className="relative min-h-0 flex-1 overflow-hidden">
          {/* The dock: the left edge on a desktop, clear of the chat's composer. */}
          <div
            className={cn(
              'pointer-events-none absolute z-40',
              narrow ? 'inset-x-0 bottom-0 h-[84px]' : 'inset-y-0 left-0 w-[80px]',
            )}
          >
            <AppDock
              columns={columns}
              inboxOpen={sheet === 'inbox'}
              mode={mode}
              disputes={conflictCount}
              lumenAvailable={lumenAvailable}
              lumenActive={lumenActive}
              position={narrow ? 'bottom' : 'left'}
              onToggle={toggle}
              onInbox={() => setSheet((was) => (was === 'inbox' ? null : 'inbox'))}
              onMode={onMode}
              onSettings={() => setAccountOpen(true)}
              onSignOut={onSignOut}
            />
          </div>

          {/* Memories | Lumen | Chat — whichever are open, in that order. */}
          {/* Between two open columns is a divider: drag it to give one more room. */}
          <div className={cn('absolute inset-0 flex p-3', narrow ? 'pb-[88px]' : 'pl-[84px]')}>
            {columns.memories && (
              <section
                aria-label="Memories"
                // A container: the toolbar compacts itself to this column's width.
                className="@container relative min-w-0 flex-1 overflow-hidden rounded-2xl ring-1 ring-white/[0.07] animate-fade-up"
              >
                {graph.error ? (
                  <Centered>
                    <p className="text-base font-medium text-foreground">Cannot load the graph</p>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted">{graph.error}</p>
                  </Centered>
                ) : (
                  <>
                    {/* The canvas, as a list: the way into the graph by keyboard
                        or screen reader. Hidden until it has focus. */}
                    <GraphList nodes={view.nodes} onSelect={selectNode} />
                    <BeliefGraph
                      data={view}
                      selectedId={selectedId}
                      onSelect={selectNode}
                      rightInset={0}
                      mode={mode}
                    />
                    {data.nodes.length > 0 && (
                      <div className="pointer-events-none absolute inset-x-0 top-3 z-10 flex justify-center px-3">
                        <GraphToolbar
                          counts={counts}
                          hidden={hiddenStatuses}
                          onToggle={toggleStatus}
                          category={category}
                          onCategory={setCategory}
                          categories={presentCategories}
                          sinceDays={sinceDays}
                          onSinceDays={setSinceDays}
                          grouping={grouping}
                          grouped={grouped}
                          onGrouping={setGrouping}
                          shown={filtered.nodes.length}
                          total={data.nodes.length}
                          pulledIn={filtered.pulledIn}
                          expandedCount={expanded.size}
                          onCollapseAll={() => setExpanded(new Set())}
                          truncated={data.truncated}
                        />
                      </div>
                    )}
                    {!graph.loading && data.nodes.length === 0 && (
                      <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-8 text-center">
                        <div className="max-w-xs animate-fade-up">
                          <Sparkles className="mx-auto mb-3 size-6 text-accent/70" />
                          <p className="text-base font-medium text-foreground">
                            {asOfDay ? 'Nothing was believed yet' : 'Your graph is empty'}
                          </p>
                          <p className="mt-1.5 text-sm leading-relaxed text-muted">
                            {asOfDay
                              ? 'No memory had been recorded by that day.'
                              : mode === 'personal'
                                ? 'Tell Lumen, or the chat, about your life — people, routines, what you’re working towards. Each fact becomes a node here, private to you.'
                                : 'Tell Lumen, or the chat, about your work — decisions, people, constraints. Each fact becomes a node here.'}
                          </p>
                        </div>
                      </div>
                    )}
                    {mode === 'workspace' && me.default_mode === 'workspace' && !asOfDay && (
                      <PersonalHint inset={0} onTry={() => onMode('personal')} />
                    )}
                    {asOfDay && (
                      <p className="glass pointer-events-none absolute top-16 left-1/2 -translate-x-1/2 rounded-full px-3.5 py-1.5 text-xs text-amber-200">
                        Beliefs as of {new Date(`${asOfDay}T12:00:00`).toLocaleDateString()} ·
                        confidence is today’s
                      </p>
                    )}
                  </>
                )}
              </section>
            )}

            {columns.memories && columns.lumen && (
              <PanelResizer
                label="Resize Lumen"
                width={lumenWidth}
                dragging={lumenPanel.dragging}
                {...lumenPanel.handleProps}
              />
            )}

            {columns.lumen && (
              // The stage is drawn in here (a portal from the chat).
              <div
                ref={setLumenHost}
                className={cn(
                  'relative min-w-0 overflow-hidden rounded-2xl ring-1 ring-white/[0.07]',
                  lumenWide ? 'flex-1' : 'shrink-0',
                )}
                style={lumenWide ? undefined : { width: lumenWidth }}
              />
            )}

            {chatBeside && columns.chat && (
              <PanelResizer
                label="Resize the chat"
                width={panel.width}
                dragging={panel.dragging}
                {...panel.handleProps}
              />
            )}

            <section
              aria-label="Chat"
              // Hidden, not unmounted: the conversation and Lumen's voice
              // session live in it, whatever is on screen.
              inert={!columns.chat}
              aria-hidden={!columns.chat}
              className={cn(
                'liquid-glass relative min-w-0 flex-col rounded-2xl',
                columns.chat ? 'flex animate-fade-up' : 'hidden',
                chatBeside ? 'shrink-0' : 'mx-auto w-full max-w-3xl',
              )}
              style={chatBeside ? { width: panel.width } : undefined}
            >
              <header className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-2.5">
                <MessageSquare className="size-4 text-accent" />
                <h2 className="text-sm font-medium">Chat</h2>
                <span className="text-xs text-muted">
                  {mode === 'personal' ? 'Personal · only you' : 'Work'}
                </span>
                <Tooltip label={'Hide the chat (Ctrl+\\)'}>
                  <button
                    type="button"
                    aria-label="Hide the chat"
                    onClick={() => toggle('chat')}
                    className="ml-auto flex size-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-white/[0.06] hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                </Tooltip>
              </header>
              <ChatPanel
                controlRef={chat}
                onLumenAvailable={setLumenAvailable}
                onLumenActive={setLumenActive}
                lumenHost={columns.lumen ? lumenHost : null}
                lumenLayout={lumenWide ? 'full' : 'docked'}
                lumenAnswerBeside={!columns.chat}
                onTakeOver={takeOver}
                onShowLumen={() => open('lumen')}
                me={me}
                mode={mode}
                asOf={asOf}
                voiceSettings={voiceSettings.data}
                openDisputes={conflicts.data?.total ?? null}
                onGraphChanged={refreshAll}
                onSelectMemory={inspect}
              />
            </section>
          </div>

          {sheet === 'memory' && (
            <Sheet
              title="Memory"
              icon={<Network className="size-4" />}
              onClose={() => setSheet(null)}
              narrow={narrow}
            >
              <MemoryDetail
                memoryId={selectedId}
                onSelect={setSelectedId}
                onMutated={refreshAll}
                userId={me.user_id}
                isAdmin={me.is_admin}
              />
            </Sheet>
          )}

          {/* The inbox is a decision to make, so it gets the middle of the
              screen: a popup over everything, not a strip at the edge. */}
          <Dialog open={sheet === 'inbox'} onOpenChange={(isOpen) => !isOpen && setSheet(null)}>
            <DialogContent
              aria-describedby={undefined}
              className="liquid-glass h-[min(86vh,820px)] max-w-3xl border-white/10 bg-transparent"
            >
              <DialogTitle className="flex items-center gap-2 pb-2">
                <Inbox className="size-4 text-accent" />
                Inbox
                {conflictCount > 0 && (
                  <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-xs font-medium text-amber-200">
                    {conflictCount} open
                  </span>
                )}
              </DialogTitle>
              <div className="flex min-h-0 flex-1 flex-col">
                <ContradictionInbox
                  resource={conflicts}
                  onResolved={graph.refresh}
                  onSelect={inspect}
                  isAdmin={me.is_admin}
                  mode={mode}
                />
              </div>
            </DialogContent>
          </Dialog>
        </div>

        <AccountDialog
          me={me}
          open={accountOpen}
          onOpenChange={setAccountOpen}
          voiceSettings={voiceSettings}
          mode={mode}
        />
      </div>
    </TooltipProvider>
  )
}

/** On a phone, the first open column of memories | Lumen | chat. */
function onlyOne(columns: Columns): Columns {
  const first = COLUMNS.find((column) => columns[column]) ?? 'lumen'
  return { memories: false, lumen: false, chat: false, [first]: true }
}

/**
 * The inbox, or one memory's detail: over whatever is on screen, from the right
 * — its own panel, not a tab beside the chat. Escape or the close button.
 */
function Sheet({
  title,
  icon,
  onClose,
  narrow,
  children,
}: {
  title: string
  icon: React.ReactNode
  onClose: () => void
  narrow: boolean
  children: React.ReactNode
}) {
  return (
    <aside
      role="dialog"
      aria-label={title}
      className={cn(
        'liquid-glass absolute z-50 flex flex-col rounded-2xl animate-fade-up',
        narrow ? 'inset-2' : 'top-3 right-3 bottom-3 w-[min(460px,calc(100%-24px))]',
      )}
    >
      <header className="flex items-center gap-2 px-4 pt-3.5 pb-2">
        <span className="text-accent">{icon}</span>
        <h2 className="text-sm font-medium">{title}</h2>
        <Tooltip label="Close (Esc)">
          <button
            type="button"
            aria-label={`Close ${title.toLowerCase()}`}
            onClick={onClose}
            className="ml-auto flex size-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-white/[0.06] hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </Tooltip>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </aside>
  )
}

const HINT_KEY = 'continuum.personal_hint_seen'

function readHintSeen(): boolean {
  try {
    return localStorage.getItem(HINT_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Accounts from before Personal mode stay on Work (requirements.md §10.5) and
 * hear about Personal once. Dismissed or tried, it never comes back.
 */
function PersonalHint({ inset, onTry }: { inset: number; onTry: () => void }) {
  const [seen, setSeen] = useState(readHintSeen)
  if (seen) return null
  const done = () => {
    setSeen(true)
    try {
      localStorage.setItem(HINT_KEY, '1')
    } catch {
      // A private window: the hint may come back next time. Harmless.
    }
  }
  return (
    <div
      role="note"
      // Its own width, up to the column's: centred at 50%, it would otherwise be
      // allowed only half of it, and wrap word by word.
      className="glass absolute top-16 flex w-max max-w-[calc(100%-1.5rem)] -translate-x-1/2 items-center gap-2 rounded-full py-1.5 pr-1.5 pl-3.5 text-xs text-muted animate-fade-up"
      style={{ left: `calc((100% - ${inset}px) / 2)` }}
    >
      <Heart className="size-3.5 text-rose-300" />
      New: a separate, private memory for your life.
      <button
        type="button"
        onClick={() => {
          done()
          onTry()
        }}
        className="rounded-full px-2 py-0.5 font-medium whitespace-nowrap text-rose-200 hover:bg-white/10"
      >
        Try Personal
      </button>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={done}
        className="rounded-full p-1 hover:bg-white/10"
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

function tallyByStatus(graph: GraphResponse): Record<MemoryStatus, number> {
  const counts: Record<MemoryStatus, number> = {
    active: 0,
    contradicted: 0,
    superseded: 0,
    archived: 0,
    pending: 0,
    declined: 0,
  }
  for (const node of graph.nodes) counts[node.status] += 1
  return counts
}
