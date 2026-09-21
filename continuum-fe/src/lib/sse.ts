/**
 * Incremental Server-Sent Events parser.
 *
 * `EventSource` cannot issue a POST, and `POST /api/v1/chat` needs a JSON body,
 * so the stream is read off `fetch` and framed here instead.
 *
 * The part that actually needs care: a network chunk has no relationship to a
 * frame boundary. One read can deliver half a `data:` line, or three frames at
 * once. So the parser keeps a buffer across pushes and only emits frames it has
 * seen terminated. Getting this wrong produces JSON.parse errors that look like
 * backend bugs, which is why it is a pure function with its own tests.
 */

export interface SSEFrame {
  event: string
  data: string
}

const FRAME_SEPARATOR = /\r?\n\r?\n/

export function createSSEParser(): (chunk: string) => SSEFrame[] {
  let buffer = ''

  return function push(chunk: string): SSEFrame[] {
    buffer += chunk

    const parts = buffer.split(FRAME_SEPARATOR)
    // The last part is either an incomplete frame or an empty string; either
    // way it is not ready to emit, so it stays in the buffer.
    buffer = parts.pop() ?? ''

    return parts.map(parseFrame).filter((frame): frame is SSEFrame => frame !== null)
  }
}

function parseFrame(raw: string): SSEFrame | null {
  let event = 'message'
  const data: string[] = []

  for (const line of raw.split(/\r?\n/)) {
    if (line === '' || line.startsWith(':')) continue // blank or comment
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    // One optional leading space after the colon is part of the framing, per
    // the spec — not part of the value.
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }

  if (data.length === 0) return null
  return { event, data: data.join('\n') }
}
