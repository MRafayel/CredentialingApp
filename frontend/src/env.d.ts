interface ImportMetaEnv {
  /** The organization local API calls act for, sent as X-Organization-Id. Local development only. */
  readonly VITE_ORGANIZATION_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
