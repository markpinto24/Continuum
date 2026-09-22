import 'highlight.js/styles/github-dark.css'

import { Check, Copy } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

/**
 * Render an assistant answer as Markdown.
 *
 * Model output is untrusted input, so this is deliberately the safe subset:
 * no `rehype-raw`, so any HTML the model emits is shown as text rather than
 * injected into the page, and react-markdown's default URL transform drops
 * `javascript:` links. Everything is styled here rather than with a typography
 * plugin so the chat panel matches the rest of the dark theme.
 *
 * Streaming is fine: an unclosed ``` fence renders as a code block that grows
 * until the closing fence arrives, rather than as stray backticks.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed text-foreground [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // `detect: false`: only highlight fences that name their language.
        // Guessing turns prose in an untagged block into rainbow noise.
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

const COMPONENTS: Components = {
  p: ({ children }) => <p className="my-2">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => <h3 className="mt-4 mb-2 text-[15px] font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-4 mb-2 text-sm font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-3 mb-1.5 text-sm font-semibold">{children}</h4>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-muted">{children}</ul>,
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-muted">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 text-muted">{children}</blockquote>
  ),
  hr: () => <hr className="my-3 border-border" />,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="scrollbar-slim my-2 overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-border bg-surface-raised px-2.5 py-1.5 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="border-b border-border/60 px-2.5 py-1.5">{children}</td>,

  // Inline code. Fenced blocks also pass through here, but inside `pre`, whose
  // styles below override these.
  code: ({ className, children }) => (
    <code
      className={cn(
        'rounded bg-surface-raised px-1 py-0.5 font-mono text-[0.85em] text-foreground',
        className,
      )}
    >
      {children}
    </code>
  ),

  pre: ({ node, children }) => {
    const code = node?.children.find(
      (child): child is Extract<typeof child, { type: 'element' }> =>
        child.type === 'element' && child.tagName === 'code',
    )
    const classes = code?.properties?.className
    const language = (Array.isArray(classes) ? classes : [])
      .map(String)
      .find((c) => c.startsWith('language-'))
      ?.slice('language-'.length)

    return (
      <CodeBlock language={language} text={code ? hastText(code) : ''}>
        {children}
      </CodeBlock>
    )
  },
}

function CodeBlock({
  language,
  text,
  children,
}: {
  language?: string
  text: string
  children: ReactNode
}) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text.replace(/\n$/, ''))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be refused (insecure origin, permissions). The
      // code is still selectable, so failing quietly is the right fallback.
    }
  }

  return (
    <div className="group my-2.5 overflow-hidden rounded-md border border-border bg-[#0d1117]">
      <div className="flex items-center justify-between border-b border-border/70 px-3 py-1">
        <span className="font-mono text-[10px] tracking-wide text-muted uppercase">
          {language ?? 'text'}
        </span>
        <button
          type="button"
          onClick={() => void copy()}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          aria-label="Copy code"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="scrollbar-slim overflow-x-auto p-3 text-[12.5px] leading-relaxed [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[12.5px]">
        {children}
      </pre>
    </div>
  )
}

/** Plain text of a hast subtree — what the copy button should put on the clipboard. */
function hastText(node: { type: string; value?: string; children?: unknown[] }): string {
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? [])
    .map((child) => hastText(child as { type: string; value?: string; children?: unknown[] }))
    .join('')
}
