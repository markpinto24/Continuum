import { ArrowRight, Loader2 } from 'lucide-react'
import { type FormEvent, useState } from 'react'

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
            : 'Your memory — what is true in your life and work, and why it changed.'}
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

/**
 * The sign-in page in Lumen's light: the aurora drifting from Work's cyan into
 * Personal's rose — both memories, before you have chosen one — behind a single
 * glass card.
 */
function Shell({ children }: { children: React.ReactNode }) {
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)')
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-[#04060a] px-4">
      <div aria-hidden className="pointer-events-none absolute inset-0">
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
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_35%,#000_100%)] opacity-80" />
      </div>

      <div className="relative w-full max-w-[400px] animate-fade-up">
        <div className="mb-7 flex flex-col items-center gap-3">
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
        <div className="liquid-glass relative rounded-3xl">{children}</div>
        <p className="mt-5 text-center text-xs text-muted/70">
          Your memory, on your own server — never shared with the team unless you share it.
        </p>
      </div>
    </div>
  )
}
