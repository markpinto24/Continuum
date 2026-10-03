/**
 * Turn a Markdown answer into text worth hearing, in chunks a speech engine
 * will actually finish.
 *
 * Read raw, an answer says "asterisk asterisk Postgres asterisk asterisk" and
 * spells out every citation. And Chromium-based browsers (Chrome, Brave, Edge)
 * stop a single long utterance after roughly fifteen seconds without an error,
 * so the text is queued as sentence-sized pieces instead.
 */

const MAX_CHUNK = 220

export function toSpeakableText(markdown: string): string {
  return (
    markdown
      // Code is for reading, not listening.
      .replace(/```[\s\S]*?```/g, ' Code block omitted. ')
      .replace(/`([^`]+)`/g, '$1')
      // [text](url) -> text; bare citations [1] [2][3] -> nothing.
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\s*\[\d+\](\[\d+\])*/g, '')
      // Headings, quotes, list markers, table pipes, emphasis, rules.
      .replace(/^\s{0,3}#{1,6}\s+/gm, '')
      .replace(/^\s*>\s?/gm, '')
      .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '')
      .replace(/\|/g, ' ')
      .replace(/^\s*[-=:]{3,}\s*$/gm, '')
      .replace(/(\*\*|__|\*|_|~~)(.+?)\1/g, '$2')
      // Whatever markup is left unpaired — common while an answer is still
      // streaming — would be read out as "asterisk".
      .replace(/[*`#~]+/g, '')
      .replace(/\s+/g, ' ')
      .trim()
  )
}

export function speechChunks(text: string): string[] {
  const sentences = text.match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g) ?? []
  const chunks: string[] = []
  let current = ''
  for (const raw of sentences) {
    const sentence = raw.trim()
    if (!sentence) continue
    if (current && current.length + sentence.length + 1 > MAX_CHUNK) {
      chunks.push(current)
      current = ''
    }
    // A single run-on sentence longer than the cap is split at word boundaries.
    if (sentence.length > MAX_CHUNK) {
      for (const word of sentence.split(' ')) {
        if (current && current.length + word.length + 1 > MAX_CHUNK) {
          chunks.push(current)
          current = ''
        }
        current = current ? `${current} ${word}` : word
      }
      continue
    }
    current = current ? `${current} ${sentence}` : sentence
  }
  if (current) chunks.push(current)
  return chunks
}

/**
 * For an answer that is still arriving: the sentences that are finished, and
 * the tail that may still grow. Speaking finished sentences as they land is what
 * makes a spoken answer start in a second instead of after the whole reply.
 *
 * An unclosed code fence cuts the text where it opens, so half a code block is
 * never read as prose.
 */
export function finishedSentences(markdown: string): string[] {
  const fences = markdown.split('```').length - 1
  const cut = fences % 2 === 1
  const settled = cut ? markdown.slice(0, markdown.lastIndexOf('```')) : markdown
  // toSpeakableText trims; keep the evidence that something followed the last
  // sentence — trailing whitespace, or the code block we just cut away.
  const text = toSpeakableText(settled) + (cut || /\s$/.test(settled) ? ' ' : '')
  // Finished means followed by whitespace: "version 3." at the very end may
  // still become "version 3.5". The last sentence is spoken when the stream ends.
  const complete = text.match(/[^.!?]+[.!?]+["')\]]*(?=\s)/g) ?? []
  return complete.map((sentence) => sentence.trim()).filter(Boolean)
}
