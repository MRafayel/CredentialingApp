import { createContext, use, useMemo } from 'react'
import { useLocation } from 'react-router'

/*
 * Back goes where the person came from. A page names itself as `here` (`<Here value={...}>`);
 * every in-app link to a record or registry page passes `useFrom()` as its router state; the
 * record and registry pages read `backFrom(state, theirDefault)`. `here.state` is the page's own
 * router state, so going back restores that page's own Back too.
 */

/** Where a back link leads: a path, the name of the page there, and that page's own state. */
export type Back = { to: string; label: string; state?: unknown }

/** How many pages back a chain of `from` remembers; older ones fall back to their default. */
const MAX_DEPTH = 8

/** The `from` of a router state, when it is one. */
function readFrom(state: unknown): Back | null {
  if (typeof state !== 'object' || state === null || !('from' in state)) return null
  const from = state.from
  if (typeof from !== 'object' || from === null || !('to' in from) || !('label' in from)) {
    return null
  }
  if (typeof from.to !== 'string' || typeof from.label !== 'string') return null
  return 'state' in from && from.state !== undefined
    ? { to: from.to, label: from.label, state: from.state }
    : { to: from.to, label: from.label }
}

/** Where Back leads: the page that opened this one when the state names it, else `fallback`. */
export function backFrom(state: unknown, fallback: Back): Back {
  return readFrom(state) ?? fallback
}

/** A `from` chain cut to `MAX_DEPTH` pages, so going back and forth never grows it forever. */
function trimmed(state: unknown, depth: number): unknown {
  const from = readFrom(state)
  if (!from || depth >= MAX_DEPTH) return undefined
  const rest = trimmed(from.state, depth + 1)
  return {
    from: rest === undefined ? { to: from.to, label: from.label } : { ...from, state: rest },
  }
}

/** The router state a link passes so the page it opens leads back here. */
export function fromHere(here: Back): { from: Back } {
  const rest = trimmed(here.state, 1)
  return {
    from: rest === undefined ? { to: here.to, label: here.label } : { ...here, state: rest },
  }
}

/** The page the links inside it lead back to; no page named means the default back. */
export const Here = createContext<Back | null>(null)

/** The current page as a place to come back to, named as the person sees its title. */
export function useHere(label: string): Back {
  const { pathname, search, state } = useLocation()
  return useMemo(() => {
    const here: Back = { to: pathname + search, label }
    return state === null || state === undefined ? here : { ...here, state }
  }, [pathname, search, state, label])
}

/** The router state for a link to a record or registry page: back to the page around it. */
export function useFrom(): { from: Back } | undefined {
  const here = use(Here)
  return here ? fromHere(here) : undefined
}
