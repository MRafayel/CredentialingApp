import { ButtonLink } from '../../components/Button.tsx'
import { VerdictUnchecked } from '@/components/illustrations'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'

export function NotFoundPage() {
  return (
    <>
      <PageBar title="Not found" />
      <PageBody>
        <PageHeader title="Not found" subtitle="This page does not exist." />
        <div className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-5 text-center">
          <VerdictUnchecked size={64} className="text-ink-faint" />
          <h2 className="text-subheading font-semibold">Nothing here</h2>
          <ButtonLink to="/providers" variant="primary">
            Go to providers
          </ButtonLink>
        </div>
      </PageBody>
    </>
  )
}
