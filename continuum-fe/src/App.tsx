import { MessageSquare, Network, Inbox } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { AppHeader } from '@/components/app-header'
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
import { api } from '@/lib/api'
import type { GraphResponse, MemoryStatus } from '@/lib/types'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }
const USER_KEY = 'continuum.user_id'

export default function App() {
  const [userId, setUserId] = useState(
    () => localStorage.getItem(USER_KEY) ?? 'mark',
  )
  const [includeArchived, setIncludeArchived] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState('chat')
  const panel = useResizablePanel()

  const health = useResource(() => api.health(), [])
  const graph = useResource(
    () => api.graph(userId, { includeArchived }),
    [userId, includeArchived],
  )
  const conflicts = useResource(() => api.conflicts(userId), [userId])

  const changeUser = useCallback((value: string) => {
    setUserId(value)
    setSelectedId(null)
    localStorage.setItem(USER_KEY, value)
  }, [])

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
          userId={userId}
          onUserId={changeUser}
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
                    <code className="text-accent">docker compose up -d</code> from the repo root.
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
                  userId={userId}
                  onGraphChanged={refreshAll}
                  onSelectMemory={inspect}
                />
              </TabsContent>

              <TabsContent value="inbox" className="min-h-0">
                <ContradictionInbox
                  userId={userId}
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
