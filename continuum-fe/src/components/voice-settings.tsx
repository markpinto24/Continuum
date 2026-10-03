import { Check, Loader2, Play, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { Resource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import type { VoiceOption, VoiceSettings } from '@/lib/types'
import { cn } from '@/lib/utils'

const SAMPLE = "Hi, I'm Lumen. This is how I'll sound when I read your answers aloud."
const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25, 1.5]

/**
 * Choose how Lumen and read-aloud sound. Every voice runs on your own server;
 * "Hear it" plays a sample before you choose. A voice is downloaded the first
 * time anyone uses it (~60 MB, a few seconds), then kept.
 */
export function VoiceSettingsPanel({
  settings,
  onSaved,
}: {
  settings: Resource<VoiceSettings>
  onSaved: () => void
}) {
  const current = settings.data
  const [voice, setVoice] = useState<string | null>(null)
  const [speed, setSpeed] = useState<number | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)

  useEffect(() => () => audio.current?.pause(), [])
  if (!current) return <p className="text-xs text-muted">Loading voices…</p>

  const chosen = voice ?? current.voice
  const pace = speed ?? current.speed
  const dirty = chosen !== current.voice || pace !== current.speed

  const preview = async (id: string) => {
    if (playing === id) {
      audio.current?.pause()
      setPlaying(null)
      return
    }
    audio.current?.pause()
    setLoading(id)
    setMessage(null)
    try {
      const blob = await api.synthesize(SAMPLE, { voice: id, speed: pace })
      const url = URL.createObjectURL(blob)
      audio.current ??= new Audio()
      const element = audio.current
      element.onended = () => {
        setPlaying(null)
        URL.revokeObjectURL(url)
      }
      element.src = url
      setPlaying(id)
      await element.play()
    } catch (cause) {
      setPlaying(null)
      setMessage({ ok: false, text: cause instanceof Error ? cause.message : String(cause) })
    } finally {
      setLoading(null)
    }
  }

  const save = async () => {
    try {
      await api.saveVoiceSettings({ voice: chosen, speed: pace })
      setVoice(null)
      setSpeed(null)
      setMessage({ ok: true, text: 'Saved. Lumen and read-aloud use it from the next sentence.' })
      onSaved()
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof Error ? cause.message : String(cause) })
    }
  }

  const groups = groupByAccent(current.voices)

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs leading-relaxed text-muted">
        The voice Lumen speaks with, and that reads answers aloud. Every voice runs on your own
        server. The first time a voice is used it downloads once (about 60 MB).
      </p>

      {groups.map(([accent, voices]) => (
        <div key={accent}>
          <p className="mb-1 text-[11px] font-medium text-muted">{accent}</p>
          <ul className="flex flex-col gap-1">
            {voices.map((option) => {
              const selected = option.id === chosen
              return (
                <li
                  key={option.id}
                  className={cn(
                    'flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs',
                    selected ? 'border-accent/50 bg-accent/5' : 'border-border',
                  )}
                >
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setVoice(option.id)}
                    className="flex flex-1 items-center gap-2 text-left"
                  >
                    <span
                      className={cn(
                        'flex size-3.5 items-center justify-center rounded-full border',
                        selected ? 'border-accent bg-accent text-background' : 'border-muted/50',
                      )}
                    >
                      {selected && <Check className="size-2.5" />}
                    </span>
                    <span className="font-medium">{option.label}</span>
                    {option.gender && <span className="text-muted">{option.gender}</span>}
                    {option.id === current.default_voice && (
                      <span className="text-[10px] text-muted/70">default</span>
                    )}
                  </button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Hear ${option.label}`}
                    onClick={() => void preview(option.id)}
                    disabled={loading !== null && loading !== option.id}
                  >
                    {loading === option.id ? (
                      <Loader2 className="animate-spin" />
                    ) : playing === option.id ? (
                      <Square />
                    ) : (
                      <Play />
                    )}
                    {playing === option.id ? 'Stop' : 'Hear it'}
                  </Button>
                </li>
              )
            })}
          </ul>
        </div>
      ))}

      <div>
        <p className="mb-1 text-[11px] font-medium text-muted">Speed</p>
        <div role="radiogroup" aria-label="Speaking speed" className="flex flex-wrap gap-1">
          {SPEEDS.map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={pace === value}
              onClick={() => setSpeed(value)}
              className={cn(
                'rounded-md border px-2 py-1 text-xs tabular-nums',
                pace === value ? 'border-accent/50 bg-accent/10 text-foreground' : 'border-border text-muted',
              )}
            >
              {value}×
            </button>
          ))}
        </div>
      </div>

      {message && (
        <p role="status" className={message.ok ? 'text-xs text-accent' : 'text-xs text-danger'}>
          {message.text}
        </p>
      )}
      <Button size="sm" className="self-start" disabled={!dirty} onClick={() => void save()}>
        Save voice
      </Button>
    </div>
  )
}

function groupByAccent(voices: VoiceOption[]): [string, VoiceOption[]][] {
  const groups = new Map<string, VoiceOption[]>()
  for (const voice of voices) groups.set(voice.accent, [...(groups.get(voice.accent) ?? []), voice])
  return [...groups.entries()]
}
