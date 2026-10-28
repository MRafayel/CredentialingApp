/**
 * The lookup's search, kept in memory above the routes so it survives a visit to a record and
 * back. Never in the URL or storage: a search can contain a provider's name.
 */
import { createContext, useContext } from 'react'

export type LookupState = {
  /** What the field holds. */
  draft: string
  /** What was last searched: the draft, trimmed, once typing pauses. */
  q: string
  setDraft: (draft: string) => void
  setQ: (q: string) => void
}

export const LookupStateContext = createContext<LookupState | null>(null)

export function useLookupState(): LookupState {
  const state = useContext(LookupStateContext)
  if (!state) throw new Error('useLookupState needs LookupStateProvider above it')
  return state
}
