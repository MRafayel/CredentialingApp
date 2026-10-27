/** Whether the window has scrolled past a threshold, for bars that frost once content slides under. */
import { useSyncExternalStore } from 'react'

function subscribe(listener: () => void) {
  window.addEventListener('scroll', listener, { passive: true })
  return () => window.removeEventListener('scroll', listener)
}

export function useScrolledPast(threshold: number): boolean {
  return useSyncExternalStore(subscribe, () => window.scrollY > threshold)
}
