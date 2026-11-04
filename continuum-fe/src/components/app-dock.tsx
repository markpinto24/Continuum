import { AudioLines, Briefcase, Heart, Inbox, LogOut, MessageSquare, Network, Settings } from 'lucide-react'

import Dock, { type DockItemData } from '@/components/reactbits/Dock'
import { LUMEN_THEMES } from '@/lib/lumen-theme'
import type { Mode } from '@/lib/types'

/**
 * The three things that can share the screen, always in this order left to
 * right: the graph, Lumen, the chat. Any of them, together or alone.
 */
export const COLUMNS = ['memories', 'lumen', 'chat'] as const
export type Column = (typeof COLUMNS)[number]
export type Columns = Record<Column, boolean>

/** Amber, as everywhere a dispute is shown — deep enough for the dock's white numerals. */
const DISPUTE_AMBER = '#d97706'

const LABEL: Record<Column, string> = { memories: 'Memories', lumen: 'Lumen', chat: 'Chat' }

/**
 * The app's navigation, a magnifying dock (React Bits). Memories, Lumen and Chat
 * are independent toggles, in the order they sit on screen; a dot marks each
 * one that is open (Lumen's stays lit while a session runs, like a running
 * app's). The inbox opens over whatever is showing. Mode switches on a click,
 * settings open on a click; holding either shows a menu (both modes, or sign
 * out) — React Bits opens menus only on a hold, like macOS, so a menu-only item
 * did nothing on a plain click.
 */
export function AppDock({
  columns,
  inboxOpen,
  mode,
  disputes,
  lumenAvailable,
  lumenActive,
  position,
  onToggle,
  onInbox,
  onMode,
  onSettings,
  onSignOut,
}: {
  columns: Columns
  inboxOpen: boolean
  mode: Mode
  disputes: number
  /** False when the server offers no voice: Lumen still has its screen, to say so. */
  lumenAvailable: boolean
  /** A session is running — its dot stays lit wherever you are. */
  lumenActive: boolean
  /** The left edge on a desktop, clear of the chat's composer; the bottom on a phone. */
  position: 'left' | 'bottom'
  onToggle: (column: Column) => void
  onInbox: () => void
  onMode: (mode: Mode) => void
  onSettings: () => void
  onSignOut: () => void
}) {
  const icon: Record<Column, React.ReactNode> = {
    memories: <Network className="size-[46%]" />,
    lumen: <AudioLines className="size-[46%]" />,
    chat: <MessageSquare className="size-[46%]" />,
  }
  const items: DockItemData[] = [
    ...COLUMNS.map((column) => ({
      icon: icon[column],
      label:
        column === 'lumen' && !lumenAvailable
          ? 'Lumen (voice is off)'
          : `${LABEL[column]} — ${columns[column] ? 'hide' : 'show'}`,
      active: columns[column] || (column === 'lumen' && lumenActive),
      onClick: () => onToggle(column),
    })),
    {
      icon: <Inbox className="size-[46%]" />,
      label: disputes > 0 ? `Inbox — ${disputes} open` : 'Inbox',
      active: inboxOpen,
      badge: disputes > 0 ? disputes : undefined,
      onClick: onInbox,
    },
    { separator: true },
    {
      icon: mode === 'personal' ? <Heart className="size-[46%]" /> : <Briefcase className="size-[46%]" />,
      label: mode === 'personal' ? 'Personal — switch to Work' : 'Work — switch to Personal',
      onClick: () => onMode(mode === 'personal' ? 'workspace' : 'personal'),
      menu: [
        {
          label: 'Personal — your life, only you',
          icon: <Heart className="size-3.5" />,
          checked: mode === 'personal',
          onClick: () => mode !== 'personal' && onMode('personal'),
        },
        {
          label: 'Work — decisions, people, the team',
          icon: <Briefcase className="size-3.5" />,
          checked: mode === 'workspace',
          onClick: () => mode !== 'workspace' && onMode('workspace'),
        },
      ],
    },
    {
      icon: <Settings className="size-[46%]" />,
      label: 'Settings',
      onClick: onSettings,
      menu: [
        { label: 'Settings — voice, keys, your data', icon: <Settings className="size-3.5" />, onClick: onSettings },
        { separator: true },
        { label: 'Sign out', icon: <LogOut className="size-3.5" />, onClick: onSignOut },
      ],
    },
  ]
  return (
    <Dock
      items={items}
      position={position}
      baseItemSize={42}
      magnification={58}
      panelHeight={56}
      gap={8}
      accentColor={LUMEN_THEMES[mode].colors.idle}
      badgeColor={DISPUTE_AMBER}
      className="pointer-events-auto"
    />
  )
}
