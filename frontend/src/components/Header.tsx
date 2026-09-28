import type { Capabilities, ViewTab } from "../types";

interface Props {
  capabilities: Capabilities | null;
  error: string | null;
  activeTab: ViewTab;
  onTabChange: (tab: ViewTab) => void;
}

/** Top bar: identity on the left, navigation tabs in the center, honest backend status on the right. */
export function Header({ capabilities, error, activeTab, onTabChange }: Props) {
  const online = capabilities !== null && error === null;
  const statusLabel = error ? "Backend unreachable" : online ? "Backend online" : "Connecting…";
  const dotClass = error ? "bg-danger" : online ? "bg-accent" : "bg-warn";

  return (
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line bg-surface-1 px-4 py-2.5 sm:px-6">
      <div className="flex items-baseline gap-3">
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">CanopyLens</h1>
        <span className="hidden text-[12px] text-ink-muted xl:inline">
          Forest Intelligence Engine
        </span>
      </div>

      {/* Navigation Tabs */}
      <nav className="flex items-center gap-1 overflow-x-auto py-1" aria-label="Engine modules">
        <TabButton active={activeTab === "survey"} onClick={() => onTabChange("survey")}>
          Survey &amp; Detection
        </TabButton>
        <TabButton active={activeTab === "benchmarks"} onClick={() => onTabChange("benchmarks")}>
          Models &amp; Benchmarks
        </TabButton>
        <TabButton active={activeTab === "vegetation"} onClick={() => onTabChange("vegetation")}>
          Vegetation Health
        </TabButton>
        <TabButton active={activeTab === "temporal"} onClick={() => onTabChange("temporal")}>
          Temporal Change
        </TabButton>
      </nav>

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

function TabButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors whitespace-nowrap cursor-pointer ${
        active
          ? "bg-surface-2 text-ink border border-line-strong shadow-xs"
          : "text-ink-muted hover:bg-surface-2/60 hover:text-ink border border-transparent"
      }`}
    >
      {children}
    </button>
  );
}
