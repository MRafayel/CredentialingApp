import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api.ts'

const RECHECK_INTERVAL_MS = 15_000

type Check =
  | { state: 'checking' }
  | { state: 'connected'; checkedAt: Date }
  | { state: 'unreachable'; checkedAt: Date }

const PRESENTATION = {
  checking: { label: 'Checking API', symbol: '…', tone: 'text-status-neutral' },
  connected: { label: 'API connected', symbol: '✓', tone: 'text-status-positive' },
  unreachable: { label: 'API unreachable', symbol: '✕', tone: 'text-status-negative' },
} as const

const clock = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

/**
 * Live backend reachability. Also the smallest example of the house rule:
 * a status is never shown without saying when it was checked.
 */
export function ApiStatus() {
  const [check, setCheck] = useState<Check>({ state: 'checking' })

  useEffect(() => {
    const controller = new AbortController()

    async function run() {
      try {
        await apiGet('/health', controller.signal)
        setCheck({ state: 'connected', checkedAt: new Date() })
      } catch {
        if (!controller.signal.aborted) {
          setCheck({ state: 'unreachable', checkedAt: new Date() })
        }
      }
    }

    void run()
    const timer = setInterval(() => void run(), RECHECK_INTERVAL_MS)
    return () => {
      controller.abort()
      clearInterval(timer)
    }
  }, [])

  const { label, symbol, tone } = PRESENTATION[check.state]

  return (
    <output className="flex items-center justify-between gap-4 text-sm">
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={check.state}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
          className={`flex items-center gap-2 font-medium ${tone}`}
        >
          <span
            aria-hidden="true"
            className="grid size-5 place-items-center rounded-full border border-current text-xs"
          >
            {symbol}
          </span>
          {label}
        </motion.span>
      </AnimatePresence>

      {check.state !== 'checking' && (
        <span className="text-ink-muted tabular-nums">
          checked{' '}
          <time dateTime={check.checkedAt.toISOString()}>{clock.format(check.checkedAt)}</time>
        </span>
      )}
    </output>
  )
}
