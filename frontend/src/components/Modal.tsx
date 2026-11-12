import { motion } from 'motion/react'
import { useEffect, useRef, type ReactNode } from 'react'
import { Icon } from './Icon.tsx'
import { settle } from './motion.ts'

/**
 * A large modal on the native <dialog>, for a whole page's content over the current one: inset
 * 4 % on desktop, the full width from 6 % down on a phone, its own scroll, and an X that stays in
 * view while it scrolls. Escape, the X and a click on the scrim close it. Render it inside
 * AnimatePresence so it fades out. `labelledBy` is the id of the content's title. A new
 * `contentKey` (another page opened inside the dialog) starts the content over: scrolled to its
 * top, with focus on the panel, since the link that opened it is gone.
 */
export function Modal({
  labelledBy,
  onClose,
  contentKey,
  children,
}: {
  labelledBy: string
  onClose: () => void
  contentKey?: string
  children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  const shownKey = useRef(contentKey)

  useEffect(() => {
    closeRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    element.showModal()
    // The dialog would focus its first control, the X; start on the content instead.
    const start = element.querySelector<HTMLElement>('[data-autofocus]') ?? panel.current
    start?.focus({ preventScroll: true })
    // The page behind stays where it is while the dialog is open.
    const root = document.documentElement
    const overflow = root.style.overflow
    root.style.overflow = 'hidden'
    const outside = (event: MouseEvent) => {
      if (event.target === element) closeRef.current()
    }
    element.addEventListener('click', outside)
    return () => {
      element.removeEventListener('click', outside)
      root.style.overflow = overflow
      element.close()
    }
  }, [])

  useEffect(() => {
    if (shownKey.current === contentKey) return
    shownKey.current = contentKey
    panel.current?.scrollTo({ top: 0 })
    panel.current?.focus({ preventScroll: true })
  }, [contentKey])

  return (
    <dialog
      ref={dialog}
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      className="fixed inset-0 m-0 h-dvh max-h-none w-full max-w-none overflow-hidden bg-transparent p-0 text-ink backdrop:bg-transparent"
    >
      <motion.div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-scrim"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.26 }}
      />
      <motion.div
        ref={panel}
        tabIndex={-1}
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.97 }}
        transition={settle}
        className="absolute inset-x-0 top-[6%] bottom-0 overflow-y-auto overscroll-contain rounded-t-hero bg-surface shadow-float outline-none lg:inset-[4%] lg:rounded-hero"
      >
        {/* Sticky and zero-height, so the X rides over the content without pushing it down. */}
        <div className="pointer-events-none sticky top-0 z-20 flex h-0 justify-end">
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="pointer-events-auto mt-3 mr-3 grid size-11 shrink-0 place-items-center rounded-full bg-surface text-ink-muted shadow-card transition-colors hover:bg-soft hover:text-ink lg:mt-5 lg:mr-5"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="px-5 pt-6 pb-[calc(env(safe-area-inset-bottom)+2.5rem)] sm:px-8 lg:px-12 lg:pt-12 lg:pb-14">
          {children}
        </div>
      </motion.div>
    </dialog>
  )
}
