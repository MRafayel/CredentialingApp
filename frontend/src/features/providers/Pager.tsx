import { Icon } from '../../components/Icon.tsx'

/** ← 1 2 3 → with "N in all". Pages near the current one, plus the first and last. */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const shown = [...new Set([1, page - 1, page, page + 1, pages])]
    .filter((n) => n >= 1 && n <= pages)
    .sort((a, b) => a - b)
  const step =
    'grid size-9 place-items-center rounded-full text-sm font-medium tabular-nums transition-colors disabled:opacity-40'
  return (
    <nav aria-label="Pages" className="flex items-center justify-between gap-3 pt-5">
      <p className="text-small text-ink-muted tabular-nums">
        {new Intl.NumberFormat().format(total)} in all
      </p>
      {pages > 1 && (
        <ul className="flex items-center gap-1">
          <li>
            <button
              type="button"
              className={`${step} text-ink-muted hover:text-ink`}
              onClick={() => onPage(page - 1)}
              disabled={page <= 1}
              aria-label="Previous page"
            >
              <Icon name="chevronLeft" />
            </button>
          </li>
          {shown.map((n, index) => (
            <li key={n} className="flex items-center gap-1">
              {index > 0 && n - (shown[index - 1] ?? n) > 1 && (
                <span aria-hidden="true" className="px-1 text-ink-muted">
                  …
                </span>
              )}
              <button
                type="button"
                className={`${step} ${n === page ? 'bg-soft text-ink' : 'text-ink-muted hover:text-ink'}`}
                onClick={() => onPage(n)}
                aria-current={n === page ? 'page' : undefined}
                aria-label={`Page ${n}`}
              >
                {n}
              </button>
            </li>
          ))}
          <li>
            <button
              type="button"
              className={`${step} text-ink-muted hover:text-ink`}
              onClick={() => onPage(page + 1)}
              disabled={page >= pages}
              aria-label="Next page"
            >
              <Icon name="chevronRight" />
            </button>
          </li>
        </ul>
      )}
    </nav>
  )
}
