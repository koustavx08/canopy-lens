/// <reference types="vite/client" />

/**
 * The environment this build was compiled with.
 *
 * Declared so that a typo in a variable name is a compile error rather than a
 * silently empty string at runtime - which, for the API base URL, means a
 * build that quietly talks to its own origin and fails only once deployed.
 */
interface ImportMetaEnv {
  /** Origin of the CanopyLens API. Empty means same-origin, via a proxy. */
  readonly VITE_API_BASE_URL?: string;
  /** Key sent as `X-API-Key`, when the backend requires one. Not a secret. */
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
