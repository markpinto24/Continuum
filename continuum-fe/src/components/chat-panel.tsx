import {
  AlertTriangle,
  Check,
  CornerDownLeft,
  Loader2,
  Mic,
  Square,
  Volume2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Markdown } from '@/components/markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { useDictation } from '@/hooks/use-dictation'
import { useResource } from '@/hooks/use-resource'
import { useSpeechSynthesis } from '@/hooks/use-speech-synthesis'
import { api, streamChat } from '@/lib/api'
import type { ChatContext, ChatDone, ChatMessage, Disagreement } from '@/lib/types'
import { cn } from '@/lib/utils'
import { describeWriteBack } from '@/lib/write-back'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  context?: ChatContext
  done?: ChatDone
  error?: string
}

/**
 * Chat against the belief graph.
 *
 * Two things here are not decoration. The `context` frame arrives before the
 * first token, so the memories in play are on screen while the answer is still
 * being written — you can see what the answer is standing on before you read it.
 * And when those memories disagree, the dispute is rendered as a banner above
 * the answer, because the backend refused to pick a side and the UI must not
 * quietly do it instead.
 */
export function ChatPanel({
  onGraphChanged,
  onSelectMemory,
}: {
  onGraphChanged: () => void
  onSelectMemory: (id: string) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const speechStatus = useResource(() => api.speechStatus(), [])
  const maxSeconds = speechStatus.data?.max_seconds ?? 120
  // Offered unless the server says dictation is off. While the status is still
  // loading the button shows; a refusal then explains itself.
  const dictationOffered = speechStatus.data?.enabled !== false

  const appendDictation = useCallback((text: string) => {
    setDraft((current) => (current.trim() ? `${current.trimEnd()} ${text}` : text))
    // Back to the keyboard, cursor at the end, ready to fix a word or send.
    requestAnimationFrame(() => {
      const input = inputRef.current
      if (!input) return
      input.focus()
      input.setSelectionRange(input.value.length, input.value.length)
    })
  }, [])
  const dictation = useDictation({ maxSeconds, onText: appendDictation })
  const reader = useSpeechSynthesis()

  // Esc abandons a recording, the same as it dismisses anything else.
  const { state: dictationState, cancel: cancelDictation } = dictation
  useEffect(() => {
    if (dictationState !== 'recording') return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') cancelDictation()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dictationState, cancelDictation])

  useEffect(() => {
    const view = scrollRef.current
    if (view) view.scrollTop = view.scrollHeight
  }, [turns])

  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(async () => {
    const question = draft.trim()
    if (!question || streaming) return

    const history: ChatMessage[] = [
      ...turns
        .filter((t) => !t.error)
        .map((t): ChatMessage => ({ role: t.role, content: t.content })),
      { role: 'user', content: question },
    ]

    setDraft('')
    setTurns((prev) => [...prev, { role: 'user', content: question }, { role: 'assistant', content: '' }])
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    /** Mutate only the assistant turn we just appended — always the last one. */
    const patch = (change: Partial<Turn>) =>
      setTurns((prev) =>
        prev.map((turn, index) => (index === prev.length - 1 ? { ...turn, ...change } : turn)),
      )

    try {
      await streamChat(
        { messages: history },
        {
          onContext: (context) => patch({ context }),
          onDelta: (text) =>
            setTurns((prev) =>
              prev.map((turn, index) =>
                index === prev.length - 1 ? { ...turn, content: turn.content + text } : turn,
              ),
            ),
          onDone: (done) => {
            patch({ done })
            // The turn was fed back through ingest, so the graph may have grown
            // a node, an edge, or a fresh dispute. Pull it again.
            if (done.remembered && done.remembered.extracted > 0) onGraphChanged()
          },
          onError: (message) => patch({ error: message }),
        },
        controller.signal,
      )
    } catch (cause) {
      if (!controller.signal.aborted) {
        patch({ error: cause instanceof Error ? cause.message : String(cause) })
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [draft, onGraphChanged, streaming, turns])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scrollRef} className="scrollbar-slim min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {turns.length === 0 ? (
          <div className="mt-6 text-center">
            <p className="text-sm font-medium text-foreground">Ask about their work</p>
            <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-muted">
              Answers are ranked by similarity × confidence × recency. Where the record
              contradicts itself, you will be told — not sold one side of it.
            </p>
          </div>
        ) : (
          <ul className="space-y-4">
            {turns.map((turn, index) => (
              <li key={index}>
                <TurnView
                  turn={turn}
                  onSelectMemory={onSelectMemory}
                  reader={reader}
                  readId={`turn-${index}`}
                  finished={!(streaming && index === turns.length - 1)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <form
        className="border-t border-border p-3"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
      >
        {dictation.state === 'recording' && (
          <div
            role="status"
            className="mb-2 flex items-center gap-2 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs"
          >
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-danger opacity-75" />
              <span className="relative inline-flex size-2 rounded-full bg-danger" />
            </span>
            <span className="text-foreground">Listening…</span>
            <span className="text-muted tabular-nums">
              {clock(dictation.elapsed)} / {clock(maxSeconds)}
            </span>
            <span className="ml-auto flex gap-1">
              <Button type="button" size="sm" variant="ghost" onClick={dictation.cancel}>
                <X />
                Cancel
              </Button>
              <Button type="button" size="sm" variant="secondary" onClick={dictation.stop}>
                <Check />
                Done
              </Button>
            </span>
          </div>
        )}
        {dictation.state === 'transcribing' && (
          <p role="status" className="mb-2 flex items-center gap-1.5 text-xs text-muted">
            <Loader2 className="size-3 animate-spin" /> Transcribing on your server…
          </p>
        )}
        {dictation.error && (
          <p role="alert" className="mb-2 flex items-start gap-1.5 text-xs text-danger">
            <span className="flex-1">{dictation.error}</span>
            <button
              type="button"
              onClick={dictation.dismissError}
              aria-label="Dismiss"
              className="text-danger/70 hover:text-danger"
            >
              <X className="size-3.5" />
            </button>
          </p>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            rows={2}
            placeholder="What database are we using?"
            className="scrollbar-slim min-h-[3.25rem] flex-1 resize-none rounded-md border border-border bg-surface px-2.5 py-2 text-sm outline-none placeholder:text-muted/70 focus-visible:ring-2 focus-visible:ring-accent/50"
          />
          {dictationOffered && (
            <Tooltip
              label={
                dictation.state === 'recording'
                  ? 'Stop and transcribe'
                  : 'Dictate a message — transcribed on your own server, not in the cloud'
              }
            >
              <Button
                type="button"
                size="icon"
                variant={dictation.state === 'recording' ? 'danger' : 'ghost'}
                aria-label={dictation.state === 'recording' ? 'Stop dictation' : 'Dictate a message'}
                aria-pressed={dictation.state === 'recording'}
                disabled={dictation.state === 'transcribing' || dictation.state === 'requesting'}
                onClick={dictation.state === 'recording' ? dictation.stop : dictation.start}
              >
                {dictation.state === 'recording' ? (
                  <Square />
                ) : dictation.state === 'idle' ? (
                  <Mic />
                ) : (
                  <Loader2 className="animate-spin" />
                )}
              </Button>
            </Tooltip>
          )}
          {streaming ? (
            <Button type="button" size="icon" variant="secondary" onClick={() => abortRef.current?.abort()}>
              <Square />
            </Button>
          ) : (
            <Button type="submit" size="icon" disabled={!draft.trim()}>
              <CornerDownLeft />
            </Button>
          )}
        </div>
        <p className="mt-1.5 text-[11px] text-muted/70">
          Every turn is fed back through ingest — it confirms what it repeats and records what is
          new.
        </p>
      </form>
    </div>
  )
}

type Reader = ReturnType<typeof useSpeechSynthesis>

function TurnView({
  turn,
  onSelectMemory,
  reader,
  readId,
  finished,
}: {
  turn: Turn
  onSelectMemory: (id: string) => void
  reader: Reader
  readId: string
  finished: boolean
}) {
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-lg rounded-br-sm bg-surface-raised px-3 py-2 text-sm leading-relaxed">
          {turn.content}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {turn.context?.disagreements.map((group, index) => (
        <DisagreementBanner key={index} group={group} onSelectMemory={onSelectMemory} />
      ))}

      {turn.context && turn.context.memories.length > 0 && (
        <details className="group rounded-md border border-border bg-surface/60 px-2.5 py-1.5">
          <summary className="cursor-pointer list-none text-[11px] text-muted select-none">
            Standing on {turn.context.memories.length}{' '}
            {turn.context.memories.length === 1 ? 'memory' : 'memories'}
            <span className="ml-1 text-muted/60 group-open:hidden">— show</span>
          </summary>
          <ul className="mt-1.5 space-y-1">
            {turn.context.memories.map((item, index) => {
              const cited = turn.done?.cited_ids.includes(item.memory.id)
              return (
                <li key={item.memory.id}>
                  <button
                    type="button"
                    onClick={() => onSelectMemory(item.memory.id)}
                    className={cn(
                      'w-full rounded px-1.5 py-1 text-left text-[11px] leading-relaxed transition-colors hover:bg-surface-raised',
                      cited ? 'text-foreground' : 'text-muted',
                    )}
                  >
                    <span className="mr-1 font-mono text-muted/70">[{index + 1}]</span>
                    {item.memory.content}
                    <span className="mt-0.5 block font-mono text-[10px] text-muted/60 tabular-nums">
                      {item.score.toFixed(3)} = sim {item.similarity.toFixed(2)} × conf{' '}
                      {item.memory.confidence.toFixed(2)} × rec {item.recency.toFixed(2)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </details>
      )}

      {turn.content ? (
        <Markdown>{turn.content}</Markdown>
      ) : (
        !turn.error && (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <Loader2 className="size-3 animate-spin" /> thinking…
          </p>
        )
      )}

      {turn.error && (
        <p className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
          {turn.error}
        </p>
      )}

      <div className="flex items-center gap-2">
        {turn.done && describeWriteBack(turn.done.remembered) && (
          <p className="flex-1 text-[11px] text-muted/70">{describeWriteBack(turn.done.remembered)}</p>
        )}
        {reader.supported && finished && turn.content && (
          <ReadAloud reader={reader} id={readId} text={turn.content} />
        )}
      </div>
    </div>
  )
}

function ReadAloud({ reader, id, text }: { reader: Reader; id: string; text: string }) {
  const speaking = reader.speakingId === id
  return (
    <button
      type="button"
      onClick={() => (speaking ? reader.stop() : reader.speak(id, text))}
      aria-label={speaking ? 'Stop reading aloud' : 'Read aloud'}
      aria-pressed={speaking}
      className={cn(
        'ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors',
        speaking ? 'text-accent' : 'text-muted/70 hover:text-foreground',
      )}
    >
      {speaking ? <Square className="size-3" /> : <Volume2 className="size-3" />}
      {speaking ? 'Stop' : 'Read aloud'}
    </button>
  )
}

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function DisagreementBanner({
  group,
  onSelectMemory,
}: {
  group: Disagreement
  onSelectMemory: (id: string) => void
}) {
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-300">
        <AlertTriangle className="size-3.5" />
        The record disagrees with itself
        {group.subject && <Badge className="border-amber-500/30 bg-transparent text-amber-300/90">{group.subject}</Badge>}
      </p>
      <ul className="mt-1.5 space-y-1">
        {group.memories.map((memory) => (
          <li key={memory.id}>
            <button
              type="button"
              onClick={() => onSelectMemory(memory.id)}
              className="w-full rounded px-1 py-0.5 text-left text-[11px] leading-relaxed text-amber-100/90 transition-colors hover:bg-amber-500/10"
            >
              {memory.content}
              <span className="ml-1 text-amber-200/50 tabular-nums">
                ({memory.confidence.toFixed(2)}, {new Date(memory.created_at).toLocaleDateString()})
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] text-amber-200/60">Resolve it in the Inbox tab.</p>
    </div>
  )
}
