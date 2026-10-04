import { ArrowRight, Loader2 } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import { BrandMark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/ui/password-input'
import { api } from '@/lib/api'
import { PASSWORD_MIN_LENGTH, newPasswordFeedback } from '@/lib/password'
import type { Me } from '@/lib/types'

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
        ? await api.setup(email, password, userId.trim() || undefined)
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
            : 'Your work memory — what you decided, and why it changed.'}
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

/** The sign-in page: one calm card over a slow constellation of beliefs. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden px-4">
      <Backdrop />
      <div className="relative w-full max-w-[400px] animate-fade-up">
        <div className="mb-6 flex flex-col items-center gap-3">
          <span className="relative flex size-14 items-center justify-center rounded-2xl bg-accent/10 ring-1 ring-accent/25">
            <span className="absolute inset-0 rounded-2xl bg-accent/20 blur-xl" />
            <BrandMark className="relative size-8 text-accent" />
          </span>
          <span className="text-sm font-medium tracking-[0.2em] text-muted uppercase">
            Continuum
          </span>
        </div>
        <div className="glass rounded-2xl shadow-2xl shadow-black/40">{children}</div>
        <p className="mt-5 text-center text-xs text-muted/70">
          Runs on your own machine. Nothing leaves it.
        </p>
      </div>
    </div>
  )
}

const NODES = [
  [12, 18], [28, 72], [44, 30], [63, 82], [78, 24], [88, 60], [20, 46], [54, 56], [70, 44],
] as const
const LINKS = [
  [0, 2], [2, 4], [4, 8], [8, 5], [1, 6], [6, 2], [7, 3], [7, 8], [6, 7],
] as const

function Backdrop() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div className="absolute -top-1/3 left-1/2 size-[900px] -translate-x-1/2 rounded-full bg-accent/[0.07] blur-3xl" />
      <div className="absolute -bottom-1/2 -left-1/4 size-[700px] rounded-full bg-emerald-500/[0.05] blur-3xl" />
      {/* Lines stretch with the page; the dots are HTML so they stay round. */}
      <svg className="absolute inset-0 size-full opacity-60" preserveAspectRatio="none" viewBox="0 0 100 100">
        {LINKS.map(([a, b]) => (
          <line
            key={`${a}-${b}`}
            x1={NODES[a][0]}
            y1={NODES[a][1]}
            x2={NODES[b][0]}
            y2={NODES[b][1]}
            stroke="var(--color-accent)"
            strokeOpacity="0.14"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      {NODES.map(([x, y], index) => (
        <span
          key={index}
          className="hud-breathe absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/70 shadow-[0_0_12px_var(--color-accent)]"
          style={{ left: `${x}%`, top: `${y}%`, animationDelay: `${index * 0.37}s` }}
        />
      ))}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_40%,var(--color-background)_85%)]" />
    </div>
  )
}
