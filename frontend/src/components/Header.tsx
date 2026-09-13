import type { Capabilities } from "../types";

interface Props {
  capabilities: Capabilities | null;
  error: string | null;
}

/** Top bar: identity on the left, honest backend status on the right. */
export function Header({ capabilities, error }: Props) {
  const online = capabilities !== null && error === null;
  const statusLabel = error ? "Backend unreachable" : online ? "Backend online" : "Connecting…";
  const dotClass = error ? "bg-danger" : online ? "bg-accent" : "bg-warn";

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-line bg-surface-1 px-4 py-3 sm:px-6">
      <div className="flex items-baseline gap-3">
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">CanopyLens</h1>
        <span className="hidden text-[13px] text-ink-muted sm:inline">
          Tree Crown Detection &amp; Canopy Analysis
        </span>
      </div>

      <div className="flex items-center gap-4">
        {capabilities && (
          <span className="hidden font-mono text-[11px] text-ink-faint md:inline">
            v{capabilities.version} · {capabilities.environment}
          </span>
        )}
        <span className="flex items-center gap-2 text-[12px] text-ink-muted" title={error ?? ""}>
          <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} aria-hidden />
          {statusLabel}
        </span>
      </div>
    </header>
  );
}
