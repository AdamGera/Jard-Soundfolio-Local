import type { Transcript } from "./audio"

export type TranscriptMatch = { turn: number; start: number; end: number }

export function transcriptMatches(transcript: Transcript, query: string): TranscriptMatch[] {
  const text = query.trim()
  if (!text) return []
  // Search the original strings so offsets still address the displayed words in every script.
  const pattern = text.split(/\s+/u).map(part => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+")
  const literal = new RegExp(pattern, "giu")
  const turns = transcript.segments.length ? transcript.segments : [transcript]
  const matches: TranscriptMatch[] = []
  turns.forEach((turn, index) => {
    for (const match of turn.text.matchAll(literal)) {
      matches.push({ turn: index, start: match.index, end: match.index + match[0].length })
    }
  })
  return matches
}
