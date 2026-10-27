import type { ReactNode } from 'react'
import { VerdictListed } from '@/components/illustrations'

/** A refusal or failure, in the backend's own words: a red mark, a red hairline, ink text. */
export function ErrorNote({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-card border border-status-listed/40 bg-surface px-4 py-3 text-body text-ink"
    >
      <VerdictListed size={18} className="mt-0.5 shrink-0" />
      <p className="min-w-0 flex-1 break-words">{children}</p>
      {action}
    </div>
  )
}
