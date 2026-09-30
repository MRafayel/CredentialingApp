import { MotionConfig, motion } from 'motion/react'
import { ApiStatus } from './features/system-status/ApiStatus.tsx'

export default function App() {
  return (
    // reducedMotion="user" makes every animation honour the OS "reduce motion" setting.
    <MotionConfig reducedMotion="user">
      <main className="grid min-h-dvh place-items-center px-4">
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="w-full max-w-md rounded-2xl border border-line bg-raised p-8 shadow-sm"
        >
          <p className="text-sm font-medium text-ink-muted">Provider verification</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Credentialing</h1>
          <p className="mt-3 text-sm text-ink-muted">
            The workspace is set up. Product screens land here next.
          </p>
          <div className="mt-6 border-t border-line pt-5">
            <ApiStatus />
          </div>
        </motion.section>
      </main>
    </MotionConfig>
  )
}
