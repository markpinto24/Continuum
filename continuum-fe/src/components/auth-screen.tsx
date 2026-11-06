import { Archive, ArrowRight, History, Loader2, MessageCircleQuestion, Server } from 'lucide-react'
import { type FormEvent, useRef, useState } from 'react'

import { BeliefStory } from '@/components/belief-story'
import { BrandMark } from '@/components/brand'
import Aurora from '@/components/reactbits/Aurora'
import ShinyText from '@/components/reactbits/ShinyText'
import { Button } from '@/components/ui/button'
import { CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/ui/password-input'
import { useMediaQuery } from '@/hooks/use-media-query'
import { api } from '@/lib/api'
import { AT_REST, gsap, reducedMotion, useGSAP } from '@/lib/motion'
import { PASSWORD_MIN_LENGTH, newPasswordFeedback } from '@/lib/password'
import type { Me, Mode } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Before authentication existed the UI kept a free-text user id here and sent it
 * with every request. It is read once, to prefill setup, so the first admin
 * keeps the graph they were already using instead of starting empty.
 */
export const LEGACY_USER_KEY = 'continuum.user_id'

function legacyUserId(): string {
  try {
    return localStorage.getItem(LEGACY_USER_KEY) ?? ''
  } catch {
    return ''
  }
}

/**
 * Sign-in, or first-run setup when the instance has no account yet.
 *
 * Setup exists so `docker-compose -f local.yml up` stays the whole install: whoever opens the
 * UI first creates the admin. That is only safe on a machine strangers cannot
 * reach, so an operator can turn it off and bootstrap from the environment —
 * in which case this says so instead of offering a form that would be refused.
 */
export function AuthScreen({
  mode,
  webSetupAllowed = true,
  onSignedIn,
}: {
  mode: 'login' | 'setup'
  webSetupAllowed?: boolean
  onSignedIn: (me: Me) => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [userId, setUserId] = useState(legacyUserId)
  // A graph from before accounts is work memory, so adopting one opens in Work.
  const [use, setUse] = useState<Use>(() => (legacyUserId() ? 'work' : 'personal'))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const setup = mode === 'setup'
  const feedback = newPasswordFeedback(password, confirm)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (setup && !feedback.ready) {
      setError(feedback.mismatch ?? 'Check the password rules above.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const me = setup
        ? await api.setup(email, password, userId.trim() || undefined, USE_MODE[use])
        : await api.login(email, password)
      onSignedIn(me)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (setup && !webSetupAllowed) {
    return (
      <Shell>
        <CardHeader className="pb-6 text-center">
          <CardTitle>No account yet</CardTitle>
          <CardDescription>
            Web setup is turned off on this instance. Set <code>ADMIN_EMAIL</code> and{' '}
            <code>ADMIN_PASSWORD</code> in the root <code>.env</code> and restart the stack to
            create the first admin.
          </CardDescription>
        </CardHeader>
      </Shell>
    )
  }

  return (
    <Shell>
      <CardHeader className="items-center text-center">
        <CardTitle>{setup ? 'Create the admin account' : 'Sign in to Continuum'}</CardTitle>
        <CardDescription>
          {setup
            ? 'The first account is the admin, and can add everyone else.'
            : (
              <>
                {/* Beside the hero, the card need not repeat it. */}
                <span className="lg:hidden">
                  Your memory — what is true in your life and work, and why it changed.
                </span>
                <span className="hidden lg:inline">Welcome back. Pick up where you left off.</span>
              </>
            )}
        </CardDescription>
      </CardHeader>

      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <Field label="Email">
            <Input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field label="Password" hint={setup ? feedback.lengthHint : undefined}>
            <PasswordInput
              autoComplete={setup ? 'new-password' : 'current-password'}
              required
              minLength={setup ? PASSWORD_MIN_LENGTH : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {setup && (
            <>
              <Field label="Confirm password" error={feedback.mismatch}>
                <PasswordInput
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </Field>
              <Field
                label="What will you use Continuum for?"
                hint={USES.find((u) => u.value === use)?.hint}
              >
                <div role="radiogroup" aria-label="Use" className="grid grid-cols-3 gap-1.5">
                  {USES.map(({ value, label, hint }) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={use === value}
                      title={hint}
                      onClick={() => setUse(value)}
                      className={cn(
                        'rounded-lg border px-2 py-2 text-xs font-medium transition-colors',
                        use === value
                          ? 'border-accent/50 bg-accent/10 text-foreground'
                          : 'border-border text-muted hover:text-foreground',
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </Field>
              <Field
                label="Keep memories stored under (optional)"
                hint="Used Continuum before accounts existed? Enter that user id to keep your graph. Otherwise leave it empty."
              >
                <Input
                  value={userId}
                  placeholder="e.g. mark"
                  onChange={(e) => setUserId(e.target.value)}
                />
              </Field>
            </>
          )}

          {error && (
            <p
              role="alert"
              className="rounded-lg bg-danger/10 px-3 py-2 text-xs leading-relaxed text-danger"
            >
              {error}
            </p>
          )}

          <Button
            type="submit"
            size="lg"
            disabled={busy || (setup && !feedback.ready)}
            className="group mt-1 w-full"
          >
            {busy && <Loader2 className="animate-spin" />}
            {setup ? 'Create account' : 'Sign in'}
            {!busy && <ArrowRight className="transition-transform group-hover:translate-x-0.5" />}
          </Button>
        </form>
      </CardContent>
    </Shell>
  )
}

type Use = 'personal' | 'work' | 'both'

const USES: { value: Use; label: string; hint: string }[] = [
  { value: 'personal', label: 'Personal', hint: 'Your life: people, health, routines, goals.' },
  { value: 'work', label: 'Work', hint: 'Your work: decisions, owners, constraints, a team.' },
  {
    value: 'both',
    label: 'Both',
    hint: 'Two separate memories that never mix. Opens in Personal; switch in the header.',
  },
]

/** Where the account opens. "Both" opens in Personal: an assistant for your life first. */
const USE_MODE: Record<Use, Mode> = { personal: 'personal', work: 'workspace', both: 'personal' }

/** What the hero promises, each one a commitment the code keeps. */
const PROMISES = [
  { icon: Archive, text: 'Nothing is ever deleted' },
  { icon: MessageCircleQuestion, text: 'Unsure? It asks you' },
  { icon: History, text: 'History is append-only' },
  { icon: Server, text: 'On your own server' },
] as const

/**
 * The sign-in page in Lumen's light: the aurora drifting from Work's cyan into
 * Personal's rose — both memories, before you have chosen one. On a wide screen
 * the left half says what Continuum is, with a small belief graph telling it;
 * the right is one glass card with light travelling round its edge.
 *
 * Nothing that moves sits behind the card's glass except the aurora it always
 * had: the story is beside it, and the edge light is drawn above it, so the
 * card never re-blurs for them.
 */
function Shell({ children }: { children: React.ReactNode }) {
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)')
  // The story is only drawn where it is shown: no hidden canvas animating on a phone.
  const wide = useMediaQuery('(min-width: 1024px)')

  // The entrance, once, as one sequence: the card rises into place while the
  // story beside it arrives line by line, then the promises, then the footnote.
  // The card's glass fades itself — never an ancestor of it, which would leave
  // the blur nothing to blur until the fade ended.
  const root = useRef<HTMLDivElement>(null)
  useGSAP(
    () => {
      const host = root.current
      if (!host || reducedMotion()) return
      const all = (name: string) => host.querySelectorAll(`[data-intro="${name}"]`)
      gsap
        .timeline({
          defaults: { ease: 'power3.out' },
          onComplete: () => gsap.set(host.querySelectorAll('[data-intro]'), { clearProps: AT_REST }),
        })
        .from(all('card'), { y: 28, scale: 0.97, duration: 0.8 }, 0)
        .from(all('card-fade'), { opacity: 0, duration: 0.6, ease: 'power1.out' }, 0)
        .from(all('hero'), { y: 22, opacity: 0, duration: 0.7, stagger: 0.08 }, 0.1)
        .from(all('promise'), { y: 12, opacity: 0, duration: 0.5, stagger: 0.06 }, '-=0.35')
        .from(all('foot'), { opacity: 0, duration: 0.6 }, '-=0.3')
    },
    { scope: root },
  )

  return (
    <div ref={root} className="relative h-full overflow-x-hidden overflow-y-auto bg-[#04060a]">
      <div aria-hidden className="pointer-events-none fixed inset-0">
        <div className="absolute inset-0 opacity-70">
          <Aurora
            colorStops={['#0e7490', '#4f46e5', '#be185d']}
            amplitude={1}
            blend={0.6}
            speed={0.45}
            height={0.42}
            paused={reduced}
            mouseInteraction={false}
            dpr={0.5}
          />
        </div>
        <div
          className="absolute inset-0 opacity-[0.05]"
          style={{
            backgroundImage:
              'linear-gradient(#a5f3fc 1px, transparent 1px), linear-gradient(90deg, #a5f3fc 1px, transparent 1px)',
            backgroundSize: '48px 48px',
            maskImage: 'radial-gradient(ellipse at center, black 25%, transparent 75%)',
          }}
        />
        {/* Two still glows low on the page, so the bottom half is not plain black. */}
        <div className="absolute -bottom-40 -left-32 size-[560px] rounded-full bg-cyan-500/10 blur-[120px]" />
        <div className="absolute -right-32 -bottom-48 size-[620px] rounded-full bg-rose-500/10 blur-[130px]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_40%,#000_100%)] opacity-70" />
      </div>

      <div className="relative mx-auto grid min-h-full w-full max-w-6xl items-center gap-12 px-4 py-10 lg:grid-cols-[minmax(0,1.15fr)_400px] lg:gap-16 lg:px-10">
        {wide && (
          <section aria-label="About Continuum">
            <div data-intro="hero" className="mb-8 flex items-center gap-3">
              <span className="relative flex size-10 items-center justify-center rounded-xl bg-white/[0.05] ring-1 ring-white/15">
                <span className="absolute inset-0 rounded-xl bg-accent/25 blur-lg" />
                <BrandMark className="relative size-6 text-accent" />
              </span>
              <ShinyText
                text="CONTINUUM"
                color="#cbd5e1a6"
                shineColor="#ffffff"
                speed={4}
                className="text-sm font-medium tracking-[0.35em]"
              />
            </div>
            <p
              data-intro="hero"
              className="mb-3 text-xs font-medium tracking-[0.2em] text-accent/80 uppercase"
            >
              Long-term memory for you and your agents
            </p>
            <h1 data-intro="hero" className="max-w-xl text-4xl leading-[1.1] font-semibold tracking-tight text-foreground xl:text-5xl">
              Memory that knows{' '}
              <span className="bg-gradient-to-r from-cyan-300 via-violet-300 to-rose-300 bg-clip-text text-transparent">
                when it changed its mind.
              </span>
            </h1>
            <p data-intro="hero" className="mt-4 max-w-lg text-[15px] leading-relaxed text-muted">
              What is true in your life and work — and when something changes, the old belief is
              kept, the new one linked to it, and anything unclear is put to you.
            </p>

            <div data-intro="hero" className="mt-6 max-w-xl">
              <BeliefStory reduced={reduced} />
            </div>

            <ul className="mt-4 grid max-w-xl grid-cols-2 gap-x-6 gap-y-2.5 text-sm text-foreground/80">
              {PROMISES.map(({ icon: Icon, text }) => (
                <li key={text} data-intro="promise" className="flex items-center gap-2.5">
                  <span className="flex size-7 items-center justify-center rounded-lg bg-white/[0.04] ring-1 ring-white/10">
                    <Icon className="size-3.5 text-accent" />
                  </span>
                  {text}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="mx-auto w-full max-w-[400px]">
          {!wide && (
            <div data-intro="hero" className="mb-7 flex flex-col items-center gap-3">
              <span className="relative flex size-14 items-center justify-center rounded-2xl bg-white/[0.05] ring-1 ring-white/15 backdrop-blur-md">
                <span className="absolute inset-0 rounded-2xl bg-accent/25 blur-xl" />
                <BrandMark className="relative size-8 text-accent" />
              </span>
              <ShinyText
                text="CONTINUUM"
                color="#cbd5e1a6"
                shineColor="#ffffff"
                speed={4}
                className="text-sm font-medium tracking-[0.35em]"
              />
            </div>
          )}
          <div
            data-intro="card"
            className="relative rounded-3xl shadow-[0_0_80px_-20px] shadow-accent/30"
          >
            <div data-intro="card-fade" className="liquid-glass relative rounded-3xl">
              {children}
            </div>
            <span data-intro="card-fade" aria-hidden className="edge-light" />
          </div>
          <p data-intro="foot" className="mt-5 text-center text-xs text-muted/70">
            Your memory, on your own server — never shared with the team unless you share it.
          </p>
        </div>
      </div>
    </div>
  )
}
