import { MessageSquare, Network, Inbox } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { AccountDialog } from '@/components/account-dialog'
import { AppHeader } from '@/components/app-header'
import { AuthScreen, LEGACY_USER_KEY } from '@/components/auth-screen'
import { BeliefGraph } from '@/components/belief-graph'
import { ChatPanel } from '@/components/chat-panel'
import { ContradictionInbox } from '@/components/contradiction-inbox'
import { GraphLegend } from '@/components/graph-legend'
import { MemoryDetail } from '@/components/memory-detail'
import { PanelResizer } from '@/components/panel-resizer'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useResizablePanel } from '@/hooks/use-resizable-panel'
import { useResource } from '@/hooks/use-resource'
import { ApiError, api, onUnauthorized } from '@/lib/api'
import type { GraphResponse, Me, MemoryStatus } from '@/lib/types'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

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
      return <Centered>Connecting…</Centered>
    case 'unreachable':
      return (
        <Centered>
          <p className="text-sm font-medium text-foreground">Cannot reach the API</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">{session.message}</p>
          <p className="mt-2 text-xs leading-relaxed text-muted/70">
            Start the backend with <code className="text-accent">docker-compose -f local.yml up -d</code>{' '}
            from <code className="text-accent">continuum-be/</code>.
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
      <div className="max-w-sm text-xs text-muted">{children}</div>
    </div>
  )
}

function Workspace({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [includeArchived, setIncludeArchived] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState('chat')
  const panel = useResizablePanel()

  const [accountOpen, setAccountOpen] = useState(false)

  const health = useResource(() => api.health(), [])
  const graph = useResource(() => api.graph({ includeArchived }), [includeArchived])
  const conflicts = useResource(() => api.conflicts(), [])

  /** Any write to the graph refreshes both views — a resolve changes both. */
  const refreshAll = useCallback(() => {
    graph.refresh()
    conflicts.refresh()
  }, [conflicts, graph])

  const inspect = useCallback((id: string) => {
    setSelectedId(id)
    setTab('memory')
  }, [])

  const data = graph.data ?? EMPTY_GRAPH
  const counts = useMemo(() => tallyByStatus(data), [data])
  const conflictCount = conflicts.data?.total ?? 0

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-full flex-col">
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
        />

        <div className="flex min-h-0 flex-1">
          <main className="relative min-w-0 flex-1">
            {graph.error ? (
              <div className="flex h-full items-center justify-center px-8 text-center">
                <div className="max-w-sm">
                  <p className="text-sm font-medium text-foreground">Cannot load the graph</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{graph.error}</p>
                  <p className="mt-2 text-xs leading-relaxed text-muted/70">
                    Start the stack with{' '}
                    <code className="text-accent">docker-compose -f local.yml up -d</code> from continuum-be/.
                  </p>
                </div>
              </div>
            ) : (
              <>
                <BeliefGraph data={data} selectedId={selectedId} onSelect={setSelectedId} />
                {data.nodes.length > 0 && <GraphLegend counts={counts} />}
              </>
            )}
          </main>

          <PanelResizer
            width={panel.width}
            dragging={panel.dragging}
            {...panel.handleProps}
          />

          <aside className="flex shrink-0 flex-col" style={{ width: panel.width }}>
            <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
              <div className="p-3 pb-2">
                <TabsList className="w-full">
                  <TabsTrigger value="chat">
                    <MessageSquare className="size-3.5" />
                    Chat
                  </TabsTrigger>
                  <TabsTrigger value="inbox">
                    <Inbox className="size-3.5" />
                    Inbox
                    {conflictCount > 0 && (
                      <span className="ml-0.5 rounded-full bg-amber-500/20 px-1.5 text-[10px] text-amber-300 tabular-nums">
                        {conflictCount}
                      </span>
                    )}
                  </TabsTrigger>
                  <TabsTrigger value="memory">
                    <Network className="size-3.5" />
                    Memory
                  </TabsTrigger>
                </TabsList>
              </div>

              <TabsContent value="chat" forceMount className="min-h-0">
                <ChatPanel
                  onGraphChanged={refreshAll}
                  onSelectMemory={inspect}
                />
              </TabsContent>

              <TabsContent value="inbox" className="min-h-0">
                <ContradictionInbox
                  resource={conflicts}
                  onResolved={graph.refresh}
                  onSelect={inspect}
                />
              </TabsContent>

              <TabsContent value="memory" className="min-h-0">
                <MemoryDetail
                  memoryId={selectedId}
                  onSelect={setSelectedId}
                  onMutated={refreshAll}
                />
              </TabsContent>
            </Tabs>
          </aside>
        </div>

        <AccountDialog me={me} open={accountOpen} onOpenChange={setAccountOpen} />
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
