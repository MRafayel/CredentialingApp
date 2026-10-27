import { useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'
import { typingFrames } from '../lib/typing.ts'

/**
 * A typewriter over an empty search field, cycling through example queries. It runs only while
 * `active` (the field is empty and not focused) and never under reduced motion; the hook says
 * whether it is typing, so the field can hide its own placeholder meanwhile.
 */
export function useTypedPlaceholder(examples: readonly string[], active: boolean) {
  const reduced = useReducedMotion() ?? false
  const typing = active && !reduced && examples.length > 0
  const [text, setText] = useState('')
  const key = examples.join('\n')

  useEffect(() => {
    if (!typing) return
    const frames = typingFrames(key.split('\n'))
    let timer = 0
    const step = () => {
      const next = frames.next()
      if (next.done) return
      setText(next.value.text)
      timer = window.setTimeout(step, next.value.wait)
    }
    timer = window.setTimeout(step, 0)
    return () => {
      window.clearTimeout(timer)
      setText('')
    }
  }, [typing, key])

  return { typing, text }
}
