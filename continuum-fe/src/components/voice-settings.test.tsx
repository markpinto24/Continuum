import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VoiceSettingsPanel } from '@/components/voice-settings'
import type { Resource } from '@/hooks/use-resource'
import type { VoiceSettings } from '@/lib/types'

const api = vi.hoisted(() => ({ synthesize: vi.fn(), saveVoiceSettings: vi.fn() }))
vi.mock('@/lib/api', () => ({ api }))

const SETTINGS: VoiceSettings = {
  voice: 'en_US-lessac-medium',
  speed: 1,
  default_voice: 'en_US-lessac-medium',
  voices: [
    { id: 'en_US-lessac-medium', label: 'Lessac', accent: 'American', gender: 'female' },
    { id: 'en_GB-alan-medium', label: 'Alan', accent: 'British', gender: 'male' },
  ],
}
const resource = (): Resource<VoiceSettings> => ({ data: SETTINGS, error: null, loading: false, refresh: vi.fn() })

beforeEach(() => {
  api.synthesize.mockReset().mockResolvedValue(new Blob(['wav']))
  api.saveVoiceSettings.mockReset().mockResolvedValue(SETTINGS)
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
  vi.stubGlobal('Audio', class { src = ''; onended = null; play() { return Promise.resolve() } pause() {} })
})

describe('voice settings', () => {
  it('groups voices by accent and marks the default', () => {
    render(<VoiceSettingsPanel settings={resource()} onSaved={vi.fn()} />)
    expect(screen.getByText('American')).toBeDefined()
    expect(screen.getByText('British')).toBeDefined()
    expect(screen.getByText('default')).toBeDefined()
  })

  it('lets you hear a voice, at the chosen speed, before choosing it', async () => {
    render(<VoiceSettingsPanel settings={resource()} onSaved={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: '1.25×' }))
    await userEvent.click(screen.getByRole('button', { name: 'Hear Alan' }))
    expect(api.synthesize).toHaveBeenCalledWith(expect.stringContaining("I'm Lumen"), {
      voice: 'en_GB-alan-medium',
      speed: 1.25,
    })
  })

  it('saves only when something changed', async () => {
    const onSaved = vi.fn()
    render(<VoiceSettingsPanel settings={resource()} onSaved={onSaved} />)
    const save = screen.getByRole('button', { name: 'Save voice' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)

    await userEvent.click(screen.getByRole('radio', { name: /Alan/ }))
    await userEvent.click(save)
    expect(api.saveVoiceSettings).toHaveBeenCalledWith({ voice: 'en_GB-alan-medium', speed: 1 })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })
})
