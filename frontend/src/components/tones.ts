/** Every status colour with its symbol, its tint and its wash: colour never stands alone. */
import type { ComponentType } from 'react'
import {
  type IllustrationProps,
  VerdictBlocked,
  VerdictClear,
  VerdictFlagged,
  VerdictListed,
  VerdictReview,
  VerdictUnchecked,
} from '@/components/illustrations'
import type { Tone } from '../lib/labels.ts'

type ToneStyle = {
  /** The mark's colour. The word beside it stays ink. */
  text: string
  /** The soft circle behind the mark only (`StatusLabel` with `chip`, the verdict hero). */
  tint: string
  /** Sets --tone-wash for `bg-wash-tone`, the faint field behind the verdict. */
  wash: string
  Mark: ComponentType<IllustrationProps>
}

export const TONES: Record<Tone, ToneStyle> = {
  clear: {
    text: 'text-status-clear',
    tint: 'bg-tint-clear',
    wash: '[--tone-wash:var(--wash-clear)]',
    Mark: VerdictClear,
  },
  review: {
    text: 'text-status-review',
    tint: 'bg-tint-review',
    wash: '[--tone-wash:var(--wash-review)]',
    Mark: VerdictReview,
  },
  flagged: {
    text: 'text-status-flagged',
    tint: 'bg-tint-flagged',
    wash: '[--tone-wash:var(--wash-flagged)]',
    Mark: VerdictFlagged,
  },
  listed: {
    text: 'text-status-listed',
    tint: 'bg-tint-listed',
    wash: '[--tone-wash:var(--wash-listed)]',
    Mark: VerdictListed,
  },
  blocked: {
    text: 'text-status-blocked',
    tint: 'bg-tint-blocked',
    wash: '[--tone-wash:var(--wash-blocked)]',
    Mark: VerdictBlocked,
  },
  neutral: {
    text: 'text-ink-muted',
    tint: 'bg-tint-blocked',
    wash: '[--tone-wash:var(--wash-blocked)]',
    Mark: VerdictUnchecked,
  },
}
