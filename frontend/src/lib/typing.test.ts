import { describe, expect, it } from 'vitest'
import { exampleFrames, typingFrames, type TypingTiming } from './typing.ts'

const T: TypingTiming = { type: 1, hold: 10, erase: 2, pause: 5 }

function take<T>(frames: Iterator<T>, count: number): T[] {
  const out: T[] = []
  for (let n = 0; n < count; n += 1) {
    const next = frames.next()
    if (next.done) break
    out.push(next.value)
  }
  return out
}

describe('exampleFrames', () => {
  it('types, holds, erases, then pauses on an empty field', () => {
    expect(exampleFrames('NPI', T)).toEqual([
      { text: 'N', wait: 1 },
      { text: 'NP', wait: 1 },
      { text: 'NPI', wait: 10 },
      { text: 'NP', wait: 2 },
      { text: 'N', wait: 2 },
      { text: '', wait: 5 },
    ])
  })

  it('uses the default timing', () => {
    const frames = exampleFrames('ab')
    expect(frames.map((frame) => frame.wait)).toEqual([45, 1600, 25, 400])
  })

  it('counts characters, not code units', () => {
    expect(exampleFrames('é😀', T).map((frame) => frame.text)).toEqual(['é', 'é😀', 'é', ''])
  })
})

describe('typingFrames', () => {
  it('cycles through the examples in order and starts over', () => {
    const texts = take(typingFrames(['ab', 'c'], T), 10).map((frame) => frame.text)
    expect(texts).toEqual(['a', 'ab', 'a', '', 'c', '', 'a', 'ab', 'a', ''])
  })

  it('skips empty examples and yields nothing without any', () => {
    expect(take(typingFrames(['', 'x'], T), 4).map((frame) => frame.text)).toEqual([
      'x',
      '',
      'x',
      '',
    ])
    expect(take(typingFrames([], T), 3)).toEqual([])
    expect(take(typingFrames([''], T), 3)).toEqual([])
  })
})
