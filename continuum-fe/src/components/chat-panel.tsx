import {
  AlertTriangle,
  AudioLines,
  Check,
  CornerDownLeft,
  Loader2,
  Mic,
  Scale,
  Square,
  Users,
  Volume2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AnswerFeedback } from '@/components/answer-feedback'
import { AutopilotOverlay, type PilotStage } from '@/components/autopilot-overlay'
import { Markdown } from '@/components/markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { useAutopilot } from '@/hooks/use-autopilot'
import { useDictation } from '@/hooks/use-dictation'
import { useResource } from '@/hooks/use-resource'
import { useVoice } from '@/hooks/use-voice'
import { api, streamChat } from '@/lib/api'
import {
  ASSISTANT_NAME,
  heardEnd,
  heardInterrupt,
  isEndCommand,
  isInterruptCommand,
  readAutopilotPref,
  writeAutopilotPref,
} from '@/lib/autopilot'
import { disputeKey } from '@/lib/escalation'
import { greeting } from '@/lib/greeting'
import { finishedSentences } from '@/lib/speakable'
import {
  SHARED_SPACE,
  type ChatContext,
  type ChatDone,
  type ChatMessage,
  type Disagreement,
  type Me,
  type Memory,
  type VoiceSettings,
} from '@/lib/types'
import { cn } from '@/lib/utils'
import { describeWriteBack } from '@/lib/write-back'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  context?: ChatContext
  done?: ChatDone
  error?: string
  /** Spoken in autopilot rather than typed. */
  via?: 'voice'
  /** The session's opening line: written locally, never sent to the model. */
  kind?: 'greeting'
}

interface SendOptions {
  via?: 'voice'
  /** The whole answer so far, on every chunk — autopilot speaks it as it grows. */
  onText?: (answer: string) => void
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
  me,
  openDisputes,
  voiceSettings = null,
  onGraphChanged,
  onSelectMemory,
  asOf = null,
}: {
  me: Me
  /** Answer from what was believed then (ISO). Such turns are never remembered. */
  asOf?: string | null
  /** Unresolved disagreements, for the greeting. Null while still loading. */
  openDisputes: number | null
  /** Your chosen voice and speed (Settings → Voice). Null: the server default. */
  voiceSettings?: VoiceSettings | null
  onGraphChanged: () => void
  onSelectMemory: (id: string) => void
}) {
  // Every session opens with a greeting turn; its words are computed at render
  // (below), so it can mention the open disputes once they have loaded.
  const [turns, setTurns] = useState<Turn[]>([{ role: 'assistant', content: '', kind: 'greeting' }])
  const turnsRef = useRef(turns)
  turnsRef.current = turns
  const greetingText = greeting({ userId: me.user_id, openDisputes })
  const greetingRef = useRef(greetingText)
  greetingRef.current = greetingText
  const [draft, setDraft] = useState('')
  // What you say is remembered in your own graph unless this is on: then it
  // becomes team knowledge, signed with your email. Off at the start of every
  // session — sharing is always a choice, never a leftover.
  const [shareTurns, setShareTurns] = useState(false)
  const shareRef = useRef(shareTurns)
  shareRef.current = shareTurns
  const asOfRef = useRef(asOf)
  asOfRef.current = asOf
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  // Disputes settled from the chat, by dispute key -> what was decided. The same
  // dispute can sit above several turns; once settled, none of them offers it again.
  const [settled, setSettled] = useState<Record<string, string>>({})
  const settle = useCallback(
    (key: string, outcome: string) => {
      setSettled((current) => ({ ...current, [key]: outcome }))
      onGraphChanged()
    },
    [onGraphChanged],
  )

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
  const reader = useVoice({
    serverSynthesis: speechStatus.data?.synthesis ?? false,
    voice: voiceSettings?.voice,
    speed: voiceSettings?.speed,
  })

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

  /** Ask one question, typed or spoken. Resolves when the answer has finished. */
  const sendText = useCallback(
    async (question: string, options: SendOptions = {}): Promise<void> => {
      if (!question || abortRef.current) return

      const history: ChatMessage[] = [
        ...turnsRef.current
          .filter((t) => !t.error && t.kind !== 'greeting')
          .map((t): ChatMessage => ({ role: t.role, content: t.content })),
        { role: 'user', content: question },
      ]

      setTurns((prev) => [
        ...prev,
        { role: 'user', content: question, via: options.via },
        { role: 'assistant', content: '' },
      ])
      setStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller
      let answer = ''

      /** Mutate only the assistant turn we just appended — always the last one. */
      const patch = (change: Partial<Turn>) =>
        setTurns((prev) =>
          prev.map((turn, index) => (index === prev.length - 1 ? { ...turn, ...change } : turn)),
        )

      try {
        await streamChat(
          { messages: history, share: shareRef.current, as_of: asOfRef.current },
          {
            onContext: (context) => patch({ context }),
            onDelta: (text) => {
              answer += text
              patch({ content: answer })
              options.onText?.(answer)
            },
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
    },
    [onGraphChanged],
  )

  const send = useCallback(() => {
    const question = draft.trim()
    if (!question || streaming) return
    setDraft('')
    void sendText(question)
  }, [draft, sendText, streaming])

  // --- Autopilot: speak, pause, hear the answer --------------------------------

  const [pilotStage, setPilotStage] = useState<PilotStage>(null)
  const [pilotCaption, setPilotCaption] = useState<string | null>(null)
  const [pilotError, setPilotError] = useState<string | null>(null)
  // Lumen was on last time. Browsers allow neither the microphone loop nor
  // speech to start without a click, so the overlay asks for one.
  const [pilotNeedsTap, setPilotNeedsTap] = useState(readAutopilotPref)
  const greetedAloud = useRef(false)
  // What Lumen is saying right now, so a command phrase inside its own answer
  // is never mistaken for one spoken by the user.
  const sayingRef = useRef('')
  const handlers = useRef({ thankAndEnd: () => {}, interrupt: () => {} })

  const speakAnswerTo = useCallback(
    async (said: string) => {
      setPilotStage('thinking')
      sayingRef.current = ''
      const speech = reader.stream(`pilot-${Date.now()}`)
      await sendText(said, {
        via: 'voice',
        onText: (answer) => {
          sayingRef.current = answer
          if (finishedSentences(answer).length > 0) setPilotStage('speaking')
          speech.push(answer)
        },
      })
      setPilotStage('speaking')
      speech.end()
      await speech.done()
    },
    [reader, sendText],
  )

  const handleUtterance = useCallback(
    async (clip: Blob) => {
      setPilotStage('transcribing')
      try {
        const said = (await api.transcribe(clip)).text.trim()
        if (!said) return
        setPilotCaption(said)
        if (isEndCommand(said)) return handlers.current.thankAndEnd()
        if (isInterruptCommand(said)) return // nothing is playing to stop
        await speakAnswerTo(said)
      } finally {
        setPilotStage(null)
      }
    },
    [speakAnswerTo],
  )

  /** Heard while Lumen was busy: act on its commands, ignore everything else. */
  const handleBargeIn = useCallback(async (clip: Blob) => {
    const heard = (await api.transcribe(clip)).text.trim()
    if (!heard) return
    const fromHerself = (test: (text: string) => boolean) => test(sayingRef.current)
    if (heardEnd(heard) && !fromHerself(heardEnd)) handlers.current.thankAndEnd()
    else if (heardInterrupt(heard) && !fromHerself(heardInterrupt)) handlers.current.interrupt()
  }, [])

  const autopilot = useAutopilot({
    maxSeconds,
    onUtterance: handleUtterance,
    onBargeIn: handleBargeIn,
    onError: setPilotError,
  })

  // While Lumen thinks and speaks, listen for "stop, Lumen" / "thank you, Lumen".
  const { setBargeIn } = autopilot
  useEffect(() => {
    setBargeIn(pilotStage === 'thinking' || pilotStage === 'speaking' || pilotStage === 'greeting')
  }, [pilotStage, setBargeIn])

  const resetPilot = useCallback(() => {
    setPilotStage(null)
    setPilotCaption(null)
    setPilotNeedsTap(false)
    writeAutopilotPref(false)
  }, [])

  /** End without a word — the overlay's close, or an error path. */
  const endAutopilot = useCallback(() => {
    autopilot.stop()
    reader.stop()
    resetPilot()
  }, [autopilot, reader, resetPilot])

  /** "Thank you, Lumen": stop everything, say goodbye, leave the transcript. */
  const thankAndEnd = useCallback(() => {
    abortRef.current?.abort()
    autopilot.stop()
    reader.stop()
    resetPilot()
    const goodbye = reader.stream('goodbye')
    goodbye.push("You're welcome. Talk soon.")
    goodbye.end()
  }, [autopilot, reader, resetPilot])

  /** "Stop, Lumen": cut the answer short and listen again straight away. */
  const interruptAutopilot = useCallback(() => {
    reader.stop()
    abortRef.current?.abort()
  }, [reader])
  handlers.current = { thankAndEnd, interrupt: interruptAutopilot }

  const startAutopilot = useCallback(async () => {
    setPilotNeedsTap(false)
    setPilotError(null)
    dictation.cancel()
    reader.stop()
    if (!(await autopilot.start())) {
      writeAutopilotPref(false)
      return
    }
    writeAutopilotPref(true)
    // The session's greeting, spoken by Lumen — once per session, before it listens.
    if (!greetedAloud.current) {
      greetedAloud.current = true
      await autopilot.hold(async () => {
        setPilotStage('greeting')
        const text = greeting({ userId: me.user_id, openDisputes, speaker: ASSISTANT_NAME })
        sayingRef.current = text
        const speech = reader.stream('greeting')
        speech.push(text)
        speech.end()
        await speech.done()
        setPilotStage(null)
      })
    }
  }, [autopilot, dictation, me.user_id, openDisputes, reader])

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {(autopilot.active || pilotNeedsTap) && (
        <AutopilotOverlay
          phase={autopilot.phase}
          stage={pilotStage}
          caption={pilotCaption}
          levelRef={autopilot.levelRef}
          needsTap={pilotNeedsTap && !autopilot.active}
          onTap={() => void startAutopilot()}
          onInterrupt={interruptAutopilot}
          onThankYou={thankAndEnd}
          onClose={endAutopilot}
        />
      )}
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
                  question={previousQuestion(turns, index)}
                  greetingText={greetingText}
                  onSelectMemory={onSelectMemory}
                  settled={settled}
                  onSettled={settle}
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
        {pilotError && (
          <p role="alert" className="mb-2 flex items-start gap-1.5 text-xs text-danger">
            <span className="flex-1">{pilotError}</span>
            <button
              type="button"
              onClick={() => setPilotError(null)}
              aria-label="Dismiss"
              className="text-danger/70 hover:text-danger"
            >
              <X className="size-3.5" />
            </button>
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
          <Tooltip
            label={
              shareTurns
                ? 'Sharing: what you say here is remembered as team knowledge, signed by you'
                : 'Private: what you say is remembered in your own graph. Click to share with the team'
            }
          >
            <Button
              type="button"
              size="icon"
              variant={shareTurns ? 'secondary' : 'ghost'}
              aria-label="Share what I say with the team"
              aria-pressed={shareTurns}
              onClick={() => setShareTurns((on) => !on)}
              className={cn(shareTurns && 'text-sky-300')}
            >
              <Users />
            </Button>
          </Tooltip>
          {dictationOffered && (
            <Tooltip label="Talk to Lumen — hands-free: speak, pause, and hear the answer. Your voice stays on your own server.">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="Talk to Lumen"
                disabled={dictation.state !== 'idle'}
                onClick={() => void startAutopilot()}
              >
                <AudioLines />
              </Button>
            </Tooltip>
          )}
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
        <p className={cn('mt-1.5 text-[11px]', asOf ? 'text-amber-300/90' : 'text-muted/70')}>
          {asOf
            ? `Asking about ${new Date(asOf).toLocaleDateString()}: answers use what was believed then, and nothing you say now is remembered.`
            : shareTurns
              ? 'Sharing on: what you say is remembered as team knowledge, visible to everyone here.'
              : 'Every turn is fed back through ingest — it confirms what it repeats and records what is new.'}
        </p>
      </form>
    </div>
  )
}

type Reader = ReturnType<typeof useVoice>

/** The user's message an assistant turn answered. */
function previousQuestion(turns: Turn[], index: number): string {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (turns[i].role === 'user') return turns[i].content
  }
  return ''
}

function TurnView({
  turn,
  question,
  greetingText,
  onSelectMemory,
  settled,
  onSettled,
  reader,
  readId,
  finished,
}: {
  turn: Turn
  question: string
  greetingText: string
  onSelectMemory: (id: string) => void
  settled: Record<string, string>
  onSettled: (key: string, outcome: string) => void
  reader: Reader
  readId: string
  finished: boolean
}) {
  if (turn.kind === 'greeting') {
    return <p className="text-sm leading-relaxed text-foreground/90">{greetingText}</p>
  }

  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-lg rounded-br-sm bg-surface-raised px-3 py-2 text-sm leading-relaxed">
          {turn.via === 'voice' && (
            <Mic className="mr-1.5 inline size-3 align-[-1px] text-muted" aria-label="Spoken" />
          )}
          {turn.content}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {turn.context?.as_of && (
        <p className="text-[11px] text-amber-300/90">
          From the record as of {new Date(turn.context.as_of).toLocaleDateString()}.
        </p>
      )}
      {turn.context?.disagreements.map((group, index) => (
        <DisagreementBanner
          key={index}
          group={group}
          onSelectMemory={onSelectMemory}
          outcome={settled[disputeKey(group)]}
          onSettled={onSettled}
        />
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
                    {item.memory.user_id === SHARED_SPACE && (
                      <span className="ml-1 text-sky-300/80">· shared</span>
                    )}
                    {item.memory.kind === 'summary' && (
                      <span className="ml-1 text-violet-300/80">· summary</span>
                    )}
                    <span className="mt-0.5 block font-mono text-[10px] text-muted/60 tabular-nums">
                      {item.score.toFixed(3)} = {item.keyword > 0 ? 'max(' : ''}sim{' '}
                      {item.similarity.toFixed(2)}
                      {item.keyword > 0 && `, words ${item.keyword.toFixed(2)})`} × conf{' '}
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
      {finished && turn.content && !turn.error && question && (
        <AnswerFeedback
          question={question}
          answer={turn.content}
          context={turn.context}
          done={turn.done}
        />
      )}
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

/**
 * An open disagreement, settled where it came up.
 *
 * The answer below already asks which side holds; these buttons let the person
 * say so without leaving the conversation. It is the same decision the inbox
 * records — and "both are true" is as prominent as picking a side, because it
 * is the right answer often enough. Nothing here is decided by the model: the
 * person presses the button.
 */
function DisagreementBanner({
  group,
  onSelectMemory,
  outcome,
  onSettled,
}: {
  group: Disagreement
  onSelectMemory: (id: string) => void
  outcome: string | undefined
  onSettled: (key: string, outcome: string) => void
}) {
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = disputeKey(group)

  // Your belief against the team's: settled on your side only.
  const team = group.memories.find((m) => m.user_id === SHARED_SPACE)
  const mine = group.memories.find((m) => m.user_id !== SHARED_SPACE)
  const crossGraph = Boolean(team && mine && mine.team_conflicts_with.includes(team.id))

  const decide = async (winner: Memory, keepBoth: boolean) => {
    setPending(keepBoth ? 'both' : winner.id)
    setError(null)
    try {
      if (crossGraph && team && mine) {
        const decision = keepBoth ? 'both_hold' : winner.id === team.id ? 'team_holds' : 'mine_holds'
        await api.resolveTeamConflict(mine.id, team.id, decision)
        onSettled(
          key,
          decision === 'mine_holds'
            ? 'Yours holds — shared, so the team can see it and settle it.'
            : decision === 'team_holds'
              ? 'Settled: the team’s holds.'
              : 'Kept both — each stays true.',
        )
        return
      }
      await api.resolveConflict({
        winner_id: winner.id,
        loser_ids: group.memories.filter((m) => m.id !== winner.id).map((m) => m.id),
        keep_both: keepBoth,
      })
      onSettled(key, keepBoth ? 'Kept both — each stays true.' : `Settled: “${winner.content}” holds.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  if (outcome) {
    return (
      <p className="flex items-center gap-1.5 rounded-md border border-accent/30 bg-accent/10 px-2.5 py-1.5 text-[11px] text-accent">
        <Check className="size-3.5" />
        {outcome} Recorded as a decision the resolver learns from.
      </p>
    )
  }

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-300">
        <AlertTriangle className="size-3.5" />
        {crossGraph ? 'Your note disagrees with the team — which holds?' : 'The record disagrees with itself — which holds?'}
        {group.subject && <Badge className="border-amber-500/30 bg-transparent text-amber-300/90">{group.subject}</Badge>}
      </p>
      <ul className="mt-1.5 space-y-1">
        {group.memories.map((memory) => (
          <li key={memory.id} className="flex items-start gap-1.5">
            <button
              type="button"
              onClick={() => onSelectMemory(memory.id)}
              className="flex-1 rounded px-1 py-0.5 text-left text-[11px] leading-relaxed text-amber-100/90 transition-colors hover:bg-amber-500/10"
            >
              {crossGraph && (
                <span className="mr-1 text-amber-200/60">
                  {memory.user_id === SHARED_SPACE ? 'Team:' : 'Yours:'}
                </span>
              )}
              {memory.content}
              <span className="ml-1 text-amber-200/50 tabular-nums">
                ({memory.confidence.toFixed(2)}, {new Date(memory.created_at).toLocaleDateString()})
              </span>
            </button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending !== null}
              onClick={() => void decide(memory, false)}
              className="h-6 shrink-0 px-2 text-[11px] text-amber-200 hover:text-amber-100"
            >
              {pending === memory.id ? <Loader2 className="animate-spin" /> : <Check />}
              This holds
            </Button>
          </li>
        ))}
      </ul>
      <div className="mt-1 flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={pending !== null}
          onClick={() => void decide(group.memories[0], true)}
          className="h-6 px-2 text-[11px] text-amber-200 hover:text-amber-100"
        >
          {pending === 'both' ? <Loader2 className="animate-spin" /> : <Scale />}
          Both are true
        </Button>
        {error && <span className="text-[11px] text-danger">{error}</span>}
      </div>
    </div>
  )
}
