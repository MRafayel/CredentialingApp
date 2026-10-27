/** How source monitoring reads on screen, in the backend's own vocabulary. */
import type { SourceHealth, SourceStatus } from './api.ts'
import type { Label } from './labels.ts'

export function healthLabel(health: SourceHealth): Label {
  switch (health) {
    case 'ok':
      return { tone: 'clear', word: 'OK' }
    case 'overdue':
      return { tone: 'review', word: 'Overdue' }
    case 'failing':
      return { tone: 'listed', word: 'Failing' }
    case 'never_fetched':
      return { tone: 'blocked', word: 'Never fetched' }
  }
}

/** "2 sources · 2 ok · 0 overdue · 0 failing", like the command line. */
export function sourcesSummary(sources: readonly Pick<SourceStatus, 'health'>[]): string {
  const count = (health: SourceHealth) => sources.filter((s) => s.health === health).length
  const parts = [
    `${sources.length} source${sources.length === 1 ? '' : 's'}`,
    `${count('ok')} ok`,
    `${count('overdue')} overdue`,
    `${count('failing')} failing`,
  ]
  const never = count('never_fetched')
  if (never) parts.push(`${never} never fetched`)
  return parts.join(' · ')
}
