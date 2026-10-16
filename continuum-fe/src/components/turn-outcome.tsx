import { Check, CircleHelp, Loader2, Undo2 } from 'lucide-react'
import { useState } from 'react'

import { api } from '@/lib/api'
import { answerPending } from '@/lib/pending'
import type { IngestResponse, Memory, Question, QuestionOption } from '@/lib/types'
import { cn } from '@/lib/utils'

/** Shown in full up to here; more than that is a note, not a conversation. */
const SHOWN = 3

/**
 * What a turn put in memory, in its own words, each with an Undo.
 *
 * "Stored 1 new memory" made the person open the graph to find out what was
 * remembered; for an agreement ("sounds good") the stored choice is the whole
 * point, so it is said back. Undo is "this isn't a real fact": archived, and
 * anything it had replaced comes back (requirements.md §14).
 */
export function RememberedList({
  remembered,
  onChanged,
  onSelect,
}: {
  remembered: IngestResponse
  onChanged: () => void
  onSelect: (id: string) => void
}) {
  const [undone, setUndone] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const items = remembered.created
  if (items.length === 0) return null

  const undo = async (memory: Memory) => {
    setBusy(memory.id)
    try {
      await api.rejectMemory(memory.id, 'misread', 'Undone from the chat')
      setUndone((was) => ({ ...was, [memory.id]: true }))
      onChanged()
    } finally {
      setBusy(null)
    }
  }

  return (
    <ul className="space-y-1">
      {items.slice(0, SHOWN).map((memory) => {
        const pending = memory.status === 'pending'
        return (
          <li key={memory.id} className="flex items-start gap-2 text-[11px] leading-relaxed">
            {pending ? (
              <CircleHelp className="mt-0.5 size-3 shrink-0 text-indigo-300" />
            ) : (
              <Check className="mt-0.5 size-3 shrink-0 text-emerald-400" />
            )}
            <button
              type="button"
              onClick={() => onSelect(memory.id)}
              className={cn(
                'min-w-0 flex-1 text-left hover:underline',
                undone[memory.id] ? 'text-muted/50 line-through' : 'text-muted',
              )}
            >
              <span className="text-foreground/80">
                {pending ? 'Saved as undecided: ' : 'Remembered: '}
              </span>
              {memory.content}
              {pending && !undone[memory.id] && (
                <span className="text-muted/70"> — I'll ask when it comes up</span>
              )}
            </button>
            {!undone[memory.id] && (
              <button
                type="button"
                aria-label={`Undo: ${memory.content}`}
                disabled={busy === memory.id}
                onClick={() => void undo(memory)}
                className="flex shrink-0 items-center gap-1 rounded-md px-1.5 text-muted/80 hover:bg-surface-raised hover:text-foreground"
              >
                {busy === memory.id ? <Loader2 className="size-3 animate-spin" /> : <Undo2 className="size-3" />}
                Undo
              </button>
            )}
          </li>
        )
      })}
      {items.length > SHOWN && (
        <li className="text-[11px] text-muted/60">and {items.length - SHOWN} more</li>
      )}
    </ul>
  )
}

/**
 * A question with fixed options, like Claude Code's. Each option is a server
 * action; nothing is decided from free text (commitment 2). The assistant's own
 * view is in the answer above it.
 */
export function PendingQuestionCard({
  question,
  outcome,
  onAnswered,
}: {
  question: Question
  /** Set once answered — kept by the panel, so it survives re-renders. */
  outcome?: string
  onAnswered: (outcome: string) => void
}) {
  const [busy, setBusy] = useState<QuestionOption['action'] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const answer = async (option: QuestionOption) => {
    setBusy(option.action)
    setError(null)
    try {
      const done = await answerPending(question.memory_id, option.action)
      onAnswered(done)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div
      role="group"
      aria-label="Question"
      className="rounded-xl bg-indigo-400/[0.06] p-3 ring-1 ring-indigo-300/20"
    >
      <p className="flex gap-2 text-xs leading-relaxed text-foreground/90">
        <CircleHelp className="mt-0.5 size-3.5 shrink-0 text-indigo-300" />
        {question.text}
      </p>
      {outcome ? (
        <p className="mt-2 pl-5.5 text-[11px] text-muted">{outcome}</p>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-1.5 pl-5.5">
          {question.options.map((option) => (
            <button
              key={option.action}
              type="button"
              disabled={busy !== null}
              onClick={() => void answer(option)}
              className={cn(
                'flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs ring-1 transition-colors',
                option.action === 'confirm'
                  ? 'bg-accent/15 text-accent ring-accent/30 hover:bg-accent/25'
                  : 'text-foreground/85 ring-border hover:bg-surface-raised',
              )}
            >
              {busy === option.action && <Loader2 className="size-3 animate-spin" />}
              {option.label}
            </button>
          ))}
        </div>
      )}
      {error && <p className="mt-2 pl-5.5 text-[11px] text-danger">{error}</p>}
    </div>
  )
}
