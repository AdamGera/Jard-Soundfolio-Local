import type { JSONContent } from "@tiptap/react"

export type PersonalNotes = { document: JSONContent; revision: number }

export const EMPTY_NOTES: JSONContent = { type: "doc", content: [{ type: "paragraph" }] }

export function readNoteDraft(key: string): PersonalNotes | null {
  try {
    const raw = sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

export function rememberNoteDraft(key: string, value: PersonalNotes | null) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* The in-memory draft remains available. */ }
}
