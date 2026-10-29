/**
 * The lookup's search and its state filter, kept in memory above the routes so they survive a
 * visit to a record and back. Never in the URL or storage: a search can contain a provider's
 * name.
 */
import { createContext, useContext } from 'react'

export type LookupState = {
  /** What the field holds. */
  draft: string
  /** What was last searched: the draft, trimmed, once typing pauses. */
  q: string
  /** The state the search is narrowed to (a two-letter code), chosen on the map. */
  state: string | null
  setDraft: (draft: string) => void
  setQ: (q: string) => void
  setState: (state: string | null) => void
}

export const LookupStateContext = createContext<LookupState | null>(null)

export function useLookupState(): LookupState {
  const state = useContext(LookupStateContext)
  if (!state) throw new Error('useLookupState needs LookupStateProvider above it')
  return state
}
