import { AnimatePresence, motion } from 'motion/react'
import { Button } from '../../components/Button.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise, settle, stagger } from '../../components/motion.ts'
import { Skeleton } from '../../components/Skeleton.tsx'
import { TONES } from '../../components/tones.ts'
import { STEP_ACTIONS, type Step, type StepAction, type StepState } from '../../lib/monitoring.ts'

const STATE_WORDS: Record<StepState, string> = {
  done: 'Done',
  trouble: 'Needs you',
  todo: 'To do',
  waiting: 'Not started',
}

/**
 * The provider's three steps, Identity, Screening and Monitoring. From `sm` they are three
 * centred columns on one line, the marks joined by a rule; on a phone they stack, the rule running
 * down between the marks. A done step has a tick in the clear tone, a step in trouble its status
 * mark, a step waiting on its action a ringed number, a step not reached a grey number. A step's
 * action is the NPI card's and stays secondary: the verdict hero's check is the page's one
 * primary button.
 */
export function Steps({
  steps,
  onAction,
}: {
  steps: Step[]
  onAction: (action: StepAction) => void
}) {
  return (
    <motion.ol
      aria-label="Steps"
      variants={stagger(0.06)}
      initial="hidden"
      animate="shown"
      className={ROW}
    >
      {steps.map((step, index) => (
        <StepItem
          key={step.key}
          step={step}
          number={index + 1}
          first={index === 0}
          last={index === steps.length - 1}
          onAction={onAction}
        />
      ))}
    </motion.ol>
  )
}

/** The row: stacked on a phone; from `sm` three equal columns with no gap, so the rules meet. */
const ROW = 'grid sm:mx-auto sm:w-full sm:max-w-3xl sm:grid-cols-3'

/**
 * The rule on either side of a mark. On a phone only the one below shows (none under the last
 * step); from `sm` both show, joining the neighbours' marks, and the outer ones of the first and
 * last steps keep their room but are invisible, so every mark sits at its column's centre.
 */
function Rule({ side, outer }: { side: 'before' | 'after'; outer: boolean }) {
  const phone = side === 'after' && !outer ? 'my-1.5 w-px flex-1' : 'hidden'
  const wide = side === 'before' ? 'sm:mr-3' : 'sm:ml-3'
  return (
    <span
      aria-hidden="true"
      className={`bg-line-strong ${phone} sm:my-0 sm:block sm:h-px sm:w-auto sm:flex-1 ${wide} ${outer ? 'sm:invisible' : ''}`}
    />
  )
}

function StepItem({
  step,
  number,
  first,
  last,
  onAction,
}: {
  step: Step
  number: number
  first: boolean
  last: boolean
  onAction: (action: StepAction) => void
}) {
  return (
    <motion.li variants={rise} className="flex gap-4 sm:flex-col sm:items-center sm:gap-3">
      <div className="flex flex-col items-center sm:w-full sm:flex-row">
        <Rule side="before" outer={first} />
        <StepMark step={step} number={number} />
        <Rule side="after" outer={last} />
      </div>
      <div
        className={`flex min-w-0 flex-1 items-start gap-3 sm:flex-col sm:items-center sm:gap-0 sm:px-3 sm:text-center ${last ? '' : 'pb-6 sm:pb-0'}`}
      >
        <div className="min-w-0 flex-1">
          <h3 className="text-title">
            {step.title}
            <span className="sr-only">, {STATE_WORDS[step.state]}</span>
          </h3>
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.p
              key={step.line}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={settle}
              className="mt-0.5 text-small text-ink-muted"
            >
              {step.line}
            </motion.p>
          </AnimatePresence>
        </div>
        {step.action && (
          <Button
            variant="secondary"
            icon={step.action === 'find_npi' ? 'search' : 'registry'}
            onClick={() => onAction(step.action as StepAction)}
            className="h-9 sm:mt-3"
          >
            {STEP_ACTIONS[step.action]}
          </Button>
        )}
      </div>
    </motion.li>
  )
}

/** The step's circle: a tick, its status mark, or its number; a change cross-fades. */
function StepMark({ step, number }: { step: Step; number: number }) {
  const tone = TONES[step.tone]
  const key = `${step.state}-${step.tone}`
  return (
    <span className="relative grid size-8 shrink-0 place-items-center">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={key}
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={settle}
          aria-hidden="true"
          className={`grid size-8 place-items-center rounded-full text-small font-semibold tabular-nums ${
            step.state === 'done'
              ? 'bg-tint-clear text-status-clear'
              : step.state === 'trouble'
                ? `${tone.tint} ${tone.text}`
                : step.state === 'todo'
                  ? 'border-2 border-ink text-ink'
                  : 'border border-line-strong text-ink-muted'
          }`}
        >
          {step.state === 'done' ? (
            <Icon name="check" size={16} />
          ) : step.state === 'trouble' ? (
            <tone.Mark size={17} />
          ) : (
            number
          )}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

export function StepsSkeleton() {
  return (
    <div aria-hidden="true" className={`${ROW} gap-5 sm:gap-0`}>
      {[0, 1, 2].map((index) => (
        <div key={index} className="flex gap-4 sm:flex-col sm:items-center sm:gap-3">
          <Skeleton className="size-8 shrink-0 rounded-full" />
          <div className="flex flex-col gap-2 sm:items-center">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-3 w-40" />
          </div>
        </div>
      ))}
    </div>
  )
}
