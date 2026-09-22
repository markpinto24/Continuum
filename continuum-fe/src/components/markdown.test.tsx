import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from './markdown'

describe('Markdown', () => {
  it('renders a fenced block as a code block, labelled with its language', () => {
    const { container } = render(
      <Markdown>{'```python\nfrom fastapi import FastAPI\napp = FastAPI()\n```'}</Markdown>,
    )

    const code = container.querySelector('pre code')
    expect(code?.textContent).toContain('from fastapi import FastAPI')
    expect(container.textContent).toContain('python')
    // The raw fence must not leak through as text.
    expect(container.textContent).not.toContain('```')
  })

  it('renders **bold** as bold, not as asterisks', () => {
    const { container } = render(<Markdown>{'1. **Creating a FastAPI Project:**'}</Markdown>)

    expect(container.querySelector('strong')?.textContent).toBe('Creating a FastAPI Project:')
    expect(container.textContent).not.toContain('**')
    expect(container.querySelector('ol')).not.toBeNull()
  })

  it('treats a half-streamed fence as a code block rather than stray backticks', () => {
    const { container } = render(<Markdown>{'```bash\npoetry new my-app'}</Markdown>)
    expect(container.querySelector('pre code')?.textContent).toContain('poetry new my-app')
  })

  it('never injects HTML from the model into the page', () => {
    // Model output is untrusted. A script or handler must arrive as text.
    const { container } = render(
      <Markdown>{'hello <img src=x onerror="alert(1)"> <script>alert(2)</script>'}</Markdown>,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
  })

  it('drops javascript: links', () => {
    const { container } = render(<Markdown>{'[click](javascript:alert(1))'}</Markdown>)
    const href = container.querySelector('a')?.getAttribute('href') ?? ''
    expect(href).not.toMatch(/^javascript:/i)
  })
})
