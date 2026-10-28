import { useMemo, useState, type ReactNode } from 'react'
import { LookupStateContext } from './lookupState.ts'

export function LookupStateProvider({ children }: { children: ReactNode }) {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const value = useMemo(() => ({ draft, q, setDraft, setQ }), [draft, q])
  return <LookupStateContext value={value}>{children}</LookupStateContext>
}
