/**
 * The roster's search and page, kept in memory above the routes so they survive a visit to a
 * provider and back. Never in the URL or storage: a search can contain a provider's name.
 */
import { createContext, useContext } from 'react'

export type ListState = {
  q: string
  page: number
  setQ: (q: string) => void
  setPage: (page: number) => void
}

export const ListStateContext = createContext<ListState | null>(null)

export function useListState(): ListState {
  const state = useContext(ListStateContext)
  if (!state) throw new Error('useListState needs ListStateProvider above it')
  return state
}
