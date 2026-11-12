import { useLocation, useParams } from 'react-router'
import { PageBar, PageBody } from '../../components/PageBar.tsx'
import { ApiError } from '../../lib/api.ts'
import { displayName } from '../../lib/provider.ts'
import { useRecordProfile } from '../../lib/queries.ts'
import { backFrom, Here, useHere } from '../shell/back.ts'
import { RecordDetails } from './RecordDetails.tsx'

/** A record page's one natural parent. */
const BACK = { to: '/lookup', label: 'Lookup' }

/**
 * One exclusion record and what the stored lists hold around it: its status first, then its
 * history in its list, other records of the same party, the parties and NPIs named on its row,
 * and records that only share the name (`RecordDetails`, also shown over a provider's page).
 * Back leads to the page that opened it (`state.from`), else the Lookup; the links inside lead
 * back here, under the record's name.
 */
export function RecordPage() {
  const { recordId = '' } = useParams()
  const back = backFrom(useLocation().state, BACK)
  // The same query `RecordDetails` reads (one request): the bar names a record that is missing.
  const profile = useRecordProfile(recordId)
  const here = useHere(profile.data ? displayName(profile.data.record) : 'Record')
  const missing =
    profile.isError &&
    !profile.data &&
    profile.error instanceof ApiError &&
    profile.error.status === 404
  return (
    <Here value={here}>
      <PageBar title={missing ? 'Not found' : 'Record'} back={back} />
      <PageBody>
        <RecordDetails recordId={recordId} back={back} />
      </PageBody>
    </Here>
  )
}
