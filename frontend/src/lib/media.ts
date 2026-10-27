/** Media queries as React state, for layout choices CSS alone cannot make (sheet direction). */
import { useSyncExternalStore } from 'react'

export const DESKTOP = '(min-width: 1024px)'

export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const media = matchMedia(query)
      media.addEventListener('change', listener)
      return () => media.removeEventListener('change', listener)
    },
    () => matchMedia(query).matches,
  )
}
