/** A placeholder block in the final layout's shape; the shimmer is the one repeating motion. */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={`skeleton block rounded-md ${className ?? ''}`} />
}
