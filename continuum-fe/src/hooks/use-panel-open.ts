import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'continuum.panel_open'

function read(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== '0'
  } catch {
    return true
  }
}

/**
 * Whether the sidebar is open, remembered per browser, toggled with Ctrl+\
 * (Cmd+\ on a Mac). Storage is a convenience: blocked storage just means
 * the panel starts open.
 */
export function usePanelOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpenState] = useState(read)

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
    } catch {
      // A private window: the choice lasts for this page only.
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === '\\' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault()
        setOpenState((was) => {
          try {
            localStorage.setItem(STORAGE_KEY, was ? '0' : '1')
          } catch {
            // As above.
          }
          return !was
        })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return [open, setOpen]
}
