/**
 * Pages that share a key do not transition: the add and import sheets open over the list, and
 * the registry and record dialogs over a provider's page.
 */
export function pageKey(pathname: string): string {
  if (pathname === '/providers/new' || pathname === '/providers/import') return '/providers'
  const dialog = /^(\/providers\/[^/]+)\/(?:registry|record\/[^/]+)\/?$/.exec(pathname)
  return dialog?.[1] ?? pathname
}
