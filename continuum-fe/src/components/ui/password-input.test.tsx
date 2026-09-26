import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuthScreen } from '@/components/auth-screen'
import { Field } from '@/components/ui/field'
import { PasswordInput } from '@/components/ui/password-input'

vi.mock('@/lib/api', () => ({ api: { setup: vi.fn(), login: vi.fn() } }))

describe('PasswordInput', () => {
  it('is hidden until the eye is pressed, and hides again', async () => {
    render(<Field label="Password"><PasswordInput defaultValue="hunter2hunter2" /></Field>)
    const input = screen.getByLabelText('Password')
    expect(input.getAttribute('type')).toBe('password')

    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input.getAttribute('type')).toBe('text')
    expect(screen.getByRole('button', { name: 'Hide password' }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(input.getAttribute('type')).toBe('password')
  })

  it('never submits the form it sits in', async () => {
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault())
    render(<form onSubmit={onSubmit}><PasswordInput aria-label="pw" /></form>)

    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('keeps the toggle out of the field name, and reads the hint with it', () => {
    // A wrapping <label> would name the input "Password Show password".
    render(<Field label="Password" hint="At least 10 characters."><PasswordInput /></Field>)
    const input = screen.getByLabelText('Password')
    const label = screen.getByText('Password')

    expect(label.getAttribute('for')).toBe(input.id) // points at it...
    expect(label.contains(screen.getByRole('button'))).toBe(false) // ...without wrapping the toggle

    const described = document.getElementById(input.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toBe('At least 10 characters.')
  })
})

describe('first-run setup', () => {
  it('says the passwords differ as you type, and holds the button until they match', async () => {
    render(<AuthScreen mode="setup" onSignedIn={() => {}} />)
    const create = screen.getByRole('button', { name: 'Create account' })
    expect((create as HTMLButtonElement).disabled).toBe(true)

    await userEvent.type(screen.getByLabelText('Password'), 'correct hor') // 11, as in the screenshot
    expect(screen.getByText('11 characters.')).toBeDefined()

    await userEvent.type(screen.getByLabelText('Confirm password'), 'wrongg') // 6, and diverging
    expect(screen.getByText('The passwords do not match.')).toBeDefined()
    expect(screen.getByLabelText('Confirm password').getAttribute('aria-invalid')).toBe('true')

    await userEvent.clear(screen.getByLabelText('Confirm password'))
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct hor')
    expect(screen.queryByText('The passwords do not match.')).toBeNull()
    expect((create as HTMLButtonElement).disabled).toBe(false)
  })

  it('counts down to the minimum length', async () => {
    render(<AuthScreen mode="setup" onSignedIn={() => {}} />)
    await userEvent.type(screen.getByLabelText('Password'), 'abc')
    expect(screen.getByText('At least 10 characters — 7 more.')).toBeDefined()
  })
})
