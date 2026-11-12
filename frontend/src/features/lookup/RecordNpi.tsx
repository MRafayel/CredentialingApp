import { AnimatePresence, motion } from 'motion/react'
import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { VerdictListed, VerdictReview } from '@/components/illustrations'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { settle } from '../../components/motion.ts'
import { StatusLabel } from '../../components/Status.tsx'
import { CalendarDate, DateOf } from '../../components/Time.tsx'
import { TONES } from '../../components/tones.ts'
import type { MatchEvidence, RecordProfile } from '../../lib/api.ts'
import { matchLevelLabel, npiStatusLabel, registryStatusLabel } from '../../lib/labels.ts'
import { evidenceWords, registryName, registryPlace } from '../../lib/registry.ts'
import { useFrom } from '../shell/back.ts'
import { linkReason } from './npiSearch.ts'
import { recordNpi, type RegistryLinkRow } from './recordNpi.ts'
import { RegistryLink } from './RegistryParts.tsx'

const MUTED = <span className="text-ink-muted">Not published</span>

/** Where a link comes from: the registry mirror, on the day the backend linked the record. */
function LinkedLine({ at }: { at: string }) {
  return (
    <p className="text-small text-ink-muted">
      NPPES · linked <DateOf at={at} />
    </p>
  )
}

/**
 * The facts panel's NPI: the published NPI as the headline and, under it, what the registry
 * says about it; or, for a record that publishes none, the NPIs the backend linked it to, with
 * their level and evidence, or why it linked none. The published value and the registry's
 * verdict never blend.
 */
export function NpiFactValue({ profile }: { profile: RecordProfile }) {
  const npi = recordNpi(profile)
  switch (npi.kind) {
    case 'unlinked':
      return npi.published ? (
        <span className="flex flex-wrap items-center gap-1">
          <CopyValue value={npi.published} label="NPI" className="-ml-1.5" />
          <RegistryLink npi={npi.published} className="ml-auto -mr-2" />
        </span>
      ) : (
        MUTED
      )
    case 'published': {
      const { registry } = npi.link
      return (
        <span className="flex flex-col gap-1">
          <Published npi={npi.npi} />
          <span className="flex flex-wrap items-center gap-x-1.5">
            <StatusLabel label={npiStatusLabel(registry.status)} size={14} />
            {registry.deactivation_date && (
              <span>
                since <CalendarDate value={registry.deactivation_date} />
              </span>
            )}
          </span>
          {npi.link.level === 'high' && (
            <span className="inline-flex items-center gap-1.5 text-small font-medium text-ink">
              <VerdictReview size={14} className="shrink-0" />
              {registryName(registry) ? 'Name differs in NPPES' : 'No name in NPPES'}
            </span>
          )}
          <LinkedLine at={npi.link.linked_at} />
        </span>
      )
    }
    case 'not_in_nppes':
      return (
        <span className="flex flex-col gap-1">
          <Published npi={npi.npi} />
          <StatusLabel label={registryStatusLabel('not_found')} size={14} />
          <LinkedLine at={npi.link.linked_at} />
        </span>
      )
    case 'candidates':
      return <Candidates best={npi.best} others={npi.others} />
    case 'no_candidate':
      return (
        <span className="flex flex-col gap-1">
          {MUTED}
          <span className="inline-flex items-start gap-1.5 text-small text-ink-muted">
            <TONES.neutral.Mark size={14} className={`mt-0.5 shrink-0 ${TONES.neutral.text}`} />
            {linkReason(npi.link.explanation)}
          </span>
          <LinkedLine at={npi.link.linked_at} />
        </span>
      )
  }
}

/** The NPI as the list publishes it, copyable, with the way to its registry page. */
function Published({ npi }: { npi: string }) {
  return (
    <span className="flex flex-wrap items-center gap-x-1">
      <CopyValue value={npi} label="NPI" className="-ml-1.5" />
      <span className="text-caption text-ink-muted">As published</span>
      <RegistryLink npi={npi} className="ml-auto -mr-2" />
    </span>
  )
}

/** The best link, then the others behind "N candidates". */
function Candidates({ best, others }: { best: RegistryLinkRow; others: RegistryLinkRow[] }) {
  const [open, setOpen] = useState(false)
  const listId = useId()
  return (
    <span className="flex flex-col gap-2">
      {MUTED}
      <span className="flex flex-col gap-2 rounded-control bg-soft px-3 py-3">
        <span className="text-label">Closest NPI</span>
        <LinkRow link={best} />
        {others.length > 0 && (
          <>
            <AnimatePresence initial={false}>
              {open && (
                <motion.ul
                  id={listId}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={settle}
                  className="flex flex-col overflow-hidden"
                >
                  {others.map((link) => (
                    <li key={link.npi} className="border-t border-line pt-2 pb-2 last:pb-0">
                      <LinkRow link={link} />
                    </li>
                  ))}
                </motion.ul>
              )}
            </AnimatePresence>
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              aria-controls={listId}
              className="-ml-2 inline-flex h-8 items-center gap-1 self-start rounded-full px-2 text-small font-medium text-ink-muted transition-colors hover:bg-surface hover:text-ink"
            >
              {open ? 'Hide candidates' : `${others.length + 1} candidates`}
              <motion.span animate={{ rotate: open ? 180 : 0 }} transition={settle}>
                <Icon name="chevronDown" size={15} />
              </motion.span>
            </button>
          </>
        )}
      </span>
      <LinkedLine at={best.linked_at} />
    </span>
  )
}

/** One linked NPI: its level, the registry's name (to its page), the NPI, place and evidence. */
function LinkRow({ link }: { link: RegistryLinkRow }) {
  const from = useFrom()
  const { registry } = link
  const name = registryName(registry)
  const place = registryPlace(registry)
  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-small">
        <StatusLabel label={matchLevelLabel(link.level)} size={14} />
        {registry.status === 'deactivated' && (
          <StatusLabel label={npiStatusLabel(registry.status)} size={14} />
        )}
      </span>
      <Link
        to={`/npi/${link.npi}`}
        state={from}
        className="self-start rounded-sm font-medium break-words text-ink underline-offset-4 hover:underline"
      >
        {name ?? 'Name not published'}
      </Link>
      <span className="flex flex-wrap items-center gap-x-1 text-small text-ink-muted">
        <CopyValue value={link.npi} label="NPI" className="-ml-1.5" />
        {place && <span>· {place}</span>}
      </span>
      <EvidenceLine evidence={link.evidence} />
    </span>
  )
}

/**
 * Evidence as words: what agreed in ink, what differs after a small ✕ in the listed colour.
 * The backend's own sentence stays on the registry page and in its explanation.
 */
export function EvidenceLine({ evidence }: { evidence: readonly MatchEvidence[] }) {
  const { matched, differs } = evidenceWords(evidence)
  if (!matched.length && !differs.length) return null
  return (
    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-small">
      {matched.length > 0 && (
        <span className="text-ink">
          <span className="sr-only">Matched: </span>
          {matched.join(' · ')}
        </span>
      )}
      {differs.map((word) => (
        <span key={word} className="inline-flex items-baseline gap-1 text-ink-muted">
          <span aria-hidden="true" className="font-bold text-status-listed">
            ✕
          </span>
          <span className="sr-only">Differs: </span>
          {word}
        </span>
      ))}
    </span>
  )
}

/**
 * Trouble with the published NPI, under the record's status: deactivated in NPPES, or not in
 * it at all. An active NPI adds nothing here.
 */
export function NpiTrouble({ profile }: { profile: RecordProfile }) {
  const npi = recordNpi(profile)
  if (npi.kind === 'published' && npi.link.registry.status === 'deactivated') {
    const since = npi.link.registry.deactivation_date
    return (
      <TroubleLine npi={npi.npi} mark={<VerdictListed size={14} className="shrink-0" />}>
        NPI {npi.npi} is deactivated in NPPES
        {since && (
          <>
            {' '}
            since <CalendarDate value={since} />
          </>
        )}
      </TroubleLine>
    )
  }
  if (npi.kind === 'not_in_nppes') {
    return (
      <TroubleLine npi={npi.npi} mark={<VerdictReview size={14} className="shrink-0" />}>
        NPI {npi.npi} is not in NPPES
      </TroubleLine>
    )
  }
  return null
}

function TroubleLine({
  npi,
  mark,
  children,
}: {
  npi: string
  mark: ReactNode
  children: ReactNode
}) {
  return (
    <p className="flex items-start gap-1.5 px-2 text-small font-medium text-ink">
      <span className="mt-0.5 shrink-0">{mark}</span>
      <span className="min-w-0">
        {children} <RegistryLink npi={npi} className="-my-1 ml-0.5 align-middle" />
      </span>
    </p>
  )
}
