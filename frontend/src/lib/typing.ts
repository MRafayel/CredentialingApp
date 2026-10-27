/**
 * The typewriter placeholder as data: each frame is the text to show and how long to show it
 * before the next one. Pure, so the sequence is tested without timers.
 */

export type TypingTiming = {
  /** Per character typed. */
  type: number
  /** A finished example stays this long. */
  hold: number
  /** Per character erased. */
  erase: number
  /** The empty field stays this long before the next example. */
  pause: number
}

export const TYPING: TypingTiming = { type: 45, hold: 1600, erase: 25, pause: 400 }

export type Frame = { text: string; wait: number }

/** One example: typed a character at a time, held, erased a character at a time, then a pause. */
export function exampleFrames(example: string, timing: TypingTiming = TYPING): Frame[] {
  const chars = Array.from(example)
  const frames: Frame[] = []
  for (let n = 1; n <= chars.length; n += 1) {
    frames.push({
      text: chars.slice(0, n).join(''),
      wait: n === chars.length ? timing.hold : timing.type,
    })
  }
  for (let n = chars.length - 1; n >= 0; n -= 1) {
    frames.push({ text: chars.slice(0, n).join(''), wait: n === 0 ? timing.pause : timing.erase })
  }
  return frames
}

/** Every example in turn, round and round; nothing at all when there are none. */
export function* typingFrames(
  examples: readonly string[],
  timing: TypingTiming = TYPING,
): Generator<Frame, void, undefined> {
  const shown = examples.filter((example) => example.length > 0)
  if (shown.length === 0) return
  for (let index = 0; ; index = (index + 1) % shown.length) {
    yield* exampleFrames(shown[index] ?? '', timing)
  }
}
