import { motion, useDragControls } from 'motion/react'
import { useEffect, useId, useRef, type ReactNode } from 'react'
import { DESKTOP, useMedia } from '../lib/media.ts'
import { Icon } from './Icon.tsx'
import { slide } from './motion.ts'

const CLOSE_DRAG = 110

/**
 * A modal sheet on the native <dialog>: a floating drawer from the right on desktop, a bottom
 * sheet on phone that also closes when dragged down. Render it inside AnimatePresence so it slides out.
 * Focus starts on the content's `data-autofocus` element when there is one.
 */
export function Sheet({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const desktop = useMedia(DESKTOP)
  const drag = useDragControls()
  const titleId = useId()
  const closeRef = useRef(onClose)

  useEffect(() => {
    closeRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    element.showModal()
    // The dialog would focus its first control, the close button; start on the content instead.
    element.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true })
    const outside = (event: MouseEvent) => {
      if (event.target === element) closeRef.current()
    }
    element.addEventListener('click', outside)
    return () => {
      element.removeEventListener('click', outside)
      element.close()
    }
  }, [])

  const hidden = desktop ? { x: 'calc(100% + 24px)' } : { y: '100%' }

  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
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
      <motion.section
        initial={hidden}
        animate={{ x: 0, y: 0 }}
        exit={hidden}
        transition={slide}
        drag={desktop ? false : 'y'}
        dragListener={false}
        dragControls={drag}
        dragConstraints={{ top: 0, bottom: 0 }}
        dragElastic={{ top: 0, bottom: 0.8 }}
        onDragEnd={(_, info) => {
          if (info.offset.y > CLOSE_DRAG) onClose()
        }}
        className={
          desktop
            ? 'absolute inset-y-3 right-3 flex w-[min(500px,calc(100%-24px))] flex-col overflow-hidden rounded-hero bg-surface shadow-float'
            : 'absolute inset-x-0 bottom-0 flex max-h-[94dvh] flex-col rounded-t-hero bg-surface shadow-float'
        }
      >
        <header
          onPointerDown={(event) => {
            if (!desktop) drag.start(event)
          }}
          className="relative flex shrink-0 items-center gap-3 px-5 pt-6 pb-3 touch-none lg:px-8 lg:pt-7 lg:touch-auto"
        >
          {!desktop && (
            <span
              aria-hidden="true"
              className="absolute top-2 left-1/2 h-1.5 w-10 -translate-x-1/2 rounded-full bg-line-strong"
            />
          )}
          <h2 id={titleId} className="text-subheading font-semibold">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-2 ml-auto grid size-10 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-ink"
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-6 lg:px-8">
          {children}
        </div>
        {footer && (
          <footer className="shrink-0 border-t border-line bg-surface px-5 pt-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] lg:px-8 lg:pb-6">
            {footer}
          </footer>
        )}
      </motion.section>
    </dialog>
  )
}
