import { useMemo, useState, type ReactNode } from 'react'
import { ListStateContext } from './listState.ts'

export function ListStateProvider({ children }: { children: ReactNode }) {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const value = useMemo(() => ({ q, page, setQ, setPage }), [q, page])
  return <ListStateContext value={value}>{children}</ListStateContext>
}
