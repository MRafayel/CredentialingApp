import { useMemo, useState, type ReactNode } from 'react'
import { LookupStateContext } from './lookupState.ts'

export function LookupStateProvider({ children }: { children: ReactNode }) {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [state, setState] = useState<string | null>(null)
  const value = useMemo(() => ({ draft, q, state, setDraft, setQ, setState }), [draft, q, state])
  return <LookupStateContext value={value}>{children}</LookupStateContext>
}
