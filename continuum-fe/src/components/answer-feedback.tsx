import { Check, Loader2, Search, ThumbsDown, ThumbsUp } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import type { ChatContext, ChatDone, Memory } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Rate an answer, and say which memory should have come up.
 *
 * The rating is cheap signal; the missing memory is the valuable part. It names
 * exactly what retrieval failed to find for this question, and becomes a
 * retrieval test case (`run_eval.py --retrieval`) that the ranking weights are
 * checked against. Nothing is retuned automatically.
 */
export function AnswerFeedback({
  question,
  answer,
  context,
  done,
}: {
  question: string
  answer: string
  context?: ChatContext
  done?: ChatDone
}) {
  const [rating, setRating] = useState<-1 | 1 | null>(null)
  const [searching, setSearching] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Memory[] | null>(null)
  const [missing, setMissing] = useState<Memory[]>([])
  const [state, setState] = useState<'idle' | 'busy' | 'sent'>('idle')
  const [error, setError] = useState<string | null>(null)

  const used = new Set(context?.memories.map((m) => m.memory.id) ?? [])

  const send = async (value: -1 | 0 | 1, extra: Memory[] = missing) => {
    setState('busy')
    setError(null)
    try {
      await api.rateAnswer({
        query: question,
        answer,
        rating: value,
        used_ids: [...used],
        cited_ids: done?.cited_ids ?? [],
        missing_ids: extra.map((m) => m.id),
      })
      setState('sent')
      setSearching(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setState('idle')
    }
  }

  const search = async (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim()) return
    setError(null)
    try {
      const { results: found } = await api.searchMemories(query.trim(), 6)
      setResults(found.map((r) => r.memory).filter((m) => !used.has(m.id)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  if (state === 'sent') {
    return (
      <p className="flex items-center gap-1 text-[11px] text-muted/70">
        <Check className="size-3" />
        Thanks — {missing.length > 0 ? 'kept as a retrieval test case.' : 'noted.'}
      </p>
    )
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1">
        {([1, -1] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-label={value === 1 ? 'Helpful answer' : 'Unhelpful answer'}
            aria-pressed={rating === value}
            disabled={state === 'busy'}
            onClick={() => {
              setRating(value)
              if (value === 1) void send(1)
              else setSearching(true)
            }}
            className={cn(
              'rounded p-1 transition-colors hover:bg-surface-raised',
              rating === value ? 'text-accent' : 'text-muted/60 hover:text-foreground',
            )}
          >
            {value === 1 ? <ThumbsUp className="size-3" /> : <ThumbsDown className="size-3" />}
          </button>
        ))}
        {!searching && (
          <button
            type="button"
            onClick={() => setSearching(true)}
            className="ml-1 text-[11px] text-muted/60 hover:text-foreground"
          >
            Something missing?
          </button>
        )}
      </div>

      {searching && (
        <div className="space-y-1.5 rounded-md border border-border bg-surface/60 px-2.5 py-2">
          <p className="text-[11px] leading-relaxed text-muted">
            Is there a memory this answer should have used? Find it and mark it.
          </p>
          <form onSubmit={(event) => void search(event)} className="flex gap-1.5">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your memories"
              aria-label="Search for the missing memory"
              className="h-7 text-xs"
            />
            <button type="submit" aria-label="Search" className="rounded px-1.5 text-muted hover:text-foreground">
              <Search className="size-3.5" />
            </button>
          </form>
          {results && results.length === 0 && (
            <p className="text-[11px] text-muted/70">No other memories match.</p>
          )}
          {results && results.length > 0 && (
            <ul className="space-y-1">
              {results.map((memory) => {
                const picked = missing.some((m) => m.id === memory.id)
                return (
                  <li key={memory.id}>
                    <button
                      type="button"
                      aria-pressed={picked}
                      onClick={() =>
                        setMissing((current) =>
                          picked ? current.filter((m) => m.id !== memory.id) : [...current, memory],
                        )
                      }
                      className={cn(
                        'flex w-full items-start gap-1.5 rounded border px-2 py-1 text-left text-[11px] leading-relaxed',
                        picked ? 'border-accent/50 text-foreground' : 'border-border text-muted',
                      )}
                    >
                      <Check className={cn('mt-0.5 size-3 shrink-0', !picked && 'opacity-0')} />
                      {memory.content}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={state === 'busy' || (missing.length === 0 && rating === null)}
              onClick={() => void send(rating ?? 0)}
              className="flex items-center gap-1 rounded bg-surface-raised px-2 py-1 text-[11px] text-foreground disabled:opacity-50"
            >
              {state === 'busy' && <Loader2 className="size-3 animate-spin" />}
              {missing.length > 0 ? `Send (${missing.length} should have come up)` : 'Send'}
            </button>
            <button
              type="button"
              onClick={() => setSearching(false)}
              className="px-2 py-1 text-[11px] text-muted hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-[11px] text-danger">{error}</p>}
    </div>
  )
}
