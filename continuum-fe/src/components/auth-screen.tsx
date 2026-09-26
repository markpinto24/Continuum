import { KeyRound, LogIn } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
        <CardHeader>
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
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {setup ? <KeyRound className="size-4 text-accent" /> : <LogIn className="size-4 text-accent" />}
          {setup ? 'Create the admin account' : 'Sign in to Continuum'}
        </CardTitle>
        <CardDescription>
          {setup
            ? 'This instance has no accounts yet. The first one you create is the admin, and can add everyone else.'
            : 'Your memories are private to your account. Agents connect with API keys you create after signing in.'}
        </CardDescription>
      </CardHeader>

      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-3">
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
                hint="If you used Continuum before sign-in existed, enter the user id you used — your existing graph becomes this account's. Leave empty to start fresh."
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
            <p role="alert" className="text-xs leading-relaxed text-danger">
              {error}
            </p>
          )}

          <Button type="submit" disabled={busy || (setup && !feedback.ready)} className="mt-1">
            {busy ? 'Working…' : setup ? 'Create account' : 'Sign in'}
          </Button>
        </form>
      </CardContent>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-4">
      <Card className="w-full max-w-sm">{children}</Card>
    </div>
  )
}
