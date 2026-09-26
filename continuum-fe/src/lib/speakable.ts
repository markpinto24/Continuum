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
