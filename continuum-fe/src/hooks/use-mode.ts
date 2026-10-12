import { useCallback, useEffect, useState } from 'react'

import { setMode as setRequestMode } from '@/lib/api'
import type { Me, Mode } from '@/lib/types'

const storageKey = (userId: string) => `continuum.mode.${userId}`

function remembered(me: Me): Mode {
  try {
    const value = localStorage.getItem(storageKey(me.user_id))
    if (value === 'personal' || value === 'workspace') return value
  } catch {
    // Blocked storage: the account's default it is.
  }
  return me.default_mode
}

/**
 * Which of the person's two graphs the UI shows. Remembered per browser and per
 * account; the account's `default_mode` decides the first load.
 *
 * The request mode is set *before* the state changes — in the initialiser and in
 * the setter — because children fetch in their effects, which run before this
 * component's. Set in an effect, the first requests would go to the wrong graph.
 */
export function useMode(me: Me): [Mode, (mode: Mode) => void] {
  const [mode, setModeState] = useState<Mode>(() => {
    const initial = remembered(me)
    setRequestMode(initial)
    return initial
  })

  const setMode = useCallback(
    (next: Mode) => {
      setRequestMode(next)
      setModeState(next)
      try {
        localStorage.setItem(storageKey(me.user_id), next)
      } catch {
        // A private window: the choice lasts for this page only.
      }
    },
    [me.user_id],
  )

  // The warm accent for Personal is a token override on <html>.
  useEffect(() => {
    document.documentElement.dataset.mode = mode
    return () => {
      delete document.documentElement.dataset.mode
    }
  }, [mode])

  return [mode, setMode]
}
