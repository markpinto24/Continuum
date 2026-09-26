import { Check, Copy, KeyRound, Lock, Plus, Users } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/ui/password-input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useResource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import { PASSWORD_MIN_LENGTH, newPasswordFeedback } from '@/lib/password'
import type { ApiKeyCreated, Me } from '@/lib/types'

/**
 * Where an agent should point: the API itself on :8000, not this dev server —
 * the Vite proxy is for the browser. Loopback-only unless local.yml's port changes.
 */
function apiBaseUrl(): string {
  return `${window.location.protocol}//${window.location.hostname}:8000/api/v1`
}

export function AccountDialog({
  me,
  open,
  onOpenChange,
}: {
  me: Me
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Account</DialogTitle>
        <DialogDescription>
          Signed in as {me.email}. Memory owner id <code className="text-accent">{me.user_id}</code>.
        </DialogDescription>

        <Tabs defaultValue="keys" className="flex min-h-0 flex-1 flex-col">
          <div className="px-4 pt-3">
            <TabsList className="w-full">
              <TabsTrigger value="keys">
                <KeyRound className="size-3.5" />
                API keys
              </TabsTrigger>
              <TabsTrigger value="password">
                <Lock className="size-3.5" />
                Password
              </TabsTrigger>
              {me.is_admin && (
                <TabsTrigger value="users">
                  <Users className="size-3.5" />
                  Users
                </TabsTrigger>
              )}
            </TabsList>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <TabsContent value="keys">
              <ApiKeys />
            </TabsContent>
            <TabsContent value="password">
              <PasswordForm />
            </TabsContent>
            {me.is_admin && (
              <TabsContent value="users">
                <UserAdmin selfId={me.user_id} />
              </TabsContent>
            )}
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

// --- API keys -----------------------------------------------------------------

function ApiKeys() {
  const keys = useResource(() => api.apiKeys(), [])
  const [name, setName] = useState('')
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [error, setError] = useState<string | null>(null)

  const create = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    try {
      setCreated(await api.createApiKey(name))
      setName('')
      keys.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const revoke = async (id: string) => {
    await api.revokeApiKey(id)
    keys.refresh()
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs leading-relaxed text-muted">
        Give each agent its own key. It can read and write your memories, but it cannot create
        keys, change your password or manage users — revoke it and it stops working at once.
      </p>

      <form onSubmit={create} className="flex gap-2">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="What is it for? e.g. laptop agent"
          aria-label="Key name"
          required
        />
        <Button type="submit" size="sm" className="h-8">
          <Plus />
          Create
        </Button>
      </form>
      {error && <p className="text-xs text-danger">{error}</p>}

      {created && <NewKey created={created} onDismiss={() => setCreated(null)} />}

      <ul className="flex flex-col gap-1.5">
        {(keys.data?.keys ?? []).map((key) => (
          <li
            key={key.id}
            className="flex items-center gap-2 rounded-md border border-border px-2.5 py-2 text-xs"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{key.name}</span>
                {key.revoked_at && <Badge>revoked</Badge>}
              </div>
              <div className="mt-0.5 text-[11px] text-muted tabular-nums">
                <code>{key.prefix}…</code> · created {day(key.created_at)} ·{' '}
                {key.last_used_at ? `last used ${day(key.last_used_at)}` : 'never used'}
              </div>
            </div>
            {!key.revoked_at && (
              <Button size="sm" variant="danger" onClick={() => revoke(key.id)}>
                Revoke
              </Button>
            )}
          </li>
        ))}
        {keys.data && keys.data.keys.length === 0 && (
          <li className="text-xs text-muted">No keys yet.</li>
        )}
      </ul>
    </div>
  )
}

/** The one moment the full key exists outside the agent that will hold it. */
function NewKey({ created, onDismiss }: { created: ApiKeyCreated; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)
  const example = `curl -H "Authorization: Bearer ${created.secret}" ${apiBaseUrl()}/auth/me`

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.secret)
      setCopied(true)
    } catch {
      // Clipboard needs a secure context; over plain HTTP on a LAN address the
      // key is still selectable in the box below.
    }
  }

  return (
    <div className="rounded-md border border-accent/40 bg-accent/5 p-3">
      <p className="text-xs font-medium text-foreground">
        Copy “{created.key.name}” now — it will not be shown again.
      </p>
      <div className="mt-2 flex gap-2">
        <Input readOnly value={created.secret} onFocus={(e) => e.target.select()} aria-label="New API key" />
        <Button size="sm" variant="secondary" className="h-8" onClick={copy}>
          {copied ? <Check /> : <Copy />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre className="mt-2 overflow-x-auto rounded bg-background px-2 py-1.5 text-[11px] text-muted">
        {example}
      </pre>
      <Button size="sm" variant="ghost" className="mt-1" onClick={onDismiss}>
        I have saved it
      </Button>
    </div>
  )
}

// --- Password -----------------------------------------------------------------

function PasswordForm() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const feedback = newPasswordFeedback(next, confirm)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!feedback.ready) return
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setMessage({ ok: true, text: 'Password changed. Every other browser has been signed out.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) })
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field label="Current password">
        <PasswordInput
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </Field>
      <Field label="New password" hint={feedback.lengthHint}>
        <PasswordInput
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
      </Field>
      <Field label="Confirm new password" error={feedback.mismatch}>
        <PasswordInput
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </Field>
      {message && (
        <p role="status" className={message.ok ? 'text-xs text-accent' : 'text-xs text-danger'}>
          {message.text}
        </p>
      )}
      <Button type="submit" size="sm" className="self-start" disabled={!current || !feedback.ready}>
        Change password
      </Button>
    </form>
  )
}

// --- Users (admins) -----------------------------------------------------------

function UserAdmin({ selfId }: { selfId: string }) {
  const users = useResource(() => api.users(), [])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const create = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    try {
      await api.createUser({ email, password, is_admin: isAdmin })
      setEmail('')
      setPassword('')
      setIsAdmin(false)
      users.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const toggle = async (id: string, body: { disabled?: boolean; is_admin?: boolean }) => {
    setError(null)
    try {
      await api.updateUser(id, body)
      users.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-1.5">
        {(users.data?.users ?? []).map((user) => (
          <li key={user.id} className="flex items-center gap-2 rounded-md border border-border px-2.5 py-2 text-xs">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{user.email}</span>
                {user.is_admin && <Badge>admin</Badge>}
                {user.disabled && <Badge>disabled</Badge>}
              </div>
              <code className="text-[11px] text-muted">{user.id}</code>
            </div>
            {user.id !== selfId && (
              <>
                <Button size="sm" variant="ghost" onClick={() => toggle(user.id, { is_admin: !user.is_admin })}>
                  {user.is_admin ? 'Remove admin' : 'Make admin'}
                </Button>
                <Button
                  size="sm"
                  variant={user.disabled ? 'secondary' : 'danger'}
                  onClick={() => toggle(user.id, { disabled: !user.disabled })}
                >
                  {user.disabled ? 'Enable' : 'Disable'}
                </Button>
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="text-[11px] leading-relaxed text-muted">
        Disabling signs a user out everywhere and stops their keys. Their memories are kept.
      </p>

      <form onSubmit={create} className="flex flex-col gap-2 rounded-md border border-border p-3">
        <span className="text-xs font-medium">Add a user</span>
        <Input type="email" placeholder="email" aria-label="New user email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <PasswordInput
          placeholder={`temporary password (${PASSWORD_MIN_LENGTH}+ characters)`}
          aria-label="New user password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
          Admin
        </label>
        {error && <p className="text-xs text-danger">{error}</p>}
        <Button type="submit" size="sm" className="self-start">
          <Plus />
          Add user
        </Button>
      </form>
    </div>
  )
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
