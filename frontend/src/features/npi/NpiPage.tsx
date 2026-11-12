import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { useParams } from 'react-router'
import { ButtonLink } from '../../components/Button.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise, stagger } from '../../components/motion.ts'
import { PageBar, PageBody } from '../../components/PageBar.tsx'
import { TONES } from '../../components/tones.ts'
import { isNpi } from '../../lib/registry.ts'
import { NpiDetails } from './NpiDetails.tsx'

const BACK = { to: '/lookup', label: 'Lookup' }

/**
 * One NPI as the registry knows it: the backend's status first, with the way to track it as a
 * provider and to ask the live registry, then the exclusion lists that publish this NPI and
 * what NPPES records about it. A path that is not ten digits is not an NPI.
 */
export function NpiPage() {
  const { npi = '' } = useParams()
  return isNpi(npi) ? <Registered npi={npi} /> : <NotAnNpi />
}

function Registered({ npi }: { npi: string }) {
  return (
    <>
      <PageBar title="NPI" back={BACK} />
      <PageBody>
        <NpiDetails npi={npi} />
      </PageBody>
    </>
  )
}

/** A path that cannot be an NPI, in the not-found page's calm style. */
function NotAnNpi() {
  return (
    <>
      <PageBar title="Not an NPI" back={BACK} />
      <PageBody>
        <motion.div
          variants={stagger(0.06)}
          initial="hidden"
          animate="shown"
          className="flex min-h-[calc(100dvh-220px-env(safe-area-inset-top)-env(safe-area-inset-bottom))] flex-col items-center justify-center gap-2 pb-10 text-center lg:min-h-[calc(100dvh-212px)]"
        >
          <Rise>
            <span
              aria-hidden="true"
              className={`mb-6 grid size-20 place-items-center rounded-full ${TONES.neutral.tint} ${TONES.neutral.text}`}
            >
              <Icon name="registry" size={36} />
            </span>
          </Rise>
          <Rise>
            <h1 className="text-heading">Not an NPI</h1>
          </Rise>
          <Rise>
            <p className="text-body text-ink-muted">An NPI has ten digits.</p>
          </Rise>
          <Rise>
            <ButtonLink variant="primary" icon="arrowLeft" to="/lookup" className="mt-6 h-11 px-6">
              Back to Lookup
            </ButtonLink>
          </Rise>
        </motion.div>
      </PageBody>
    </>
  )
}

function Rise({ children }: { children: ReactNode }) {
  return <motion.div variants={rise}>{children}</motion.div>
}
