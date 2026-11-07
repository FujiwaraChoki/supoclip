"use client";

import { useRef, useState, type ReactElement, type ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type SettingsSection = {
  id: string;
  label: string;
  icon: ReactNode;
  /** The current choice, shown under the label so the menu reads without opening each section. */
  value?: string;
  /** How many things are on or changed here. */
  count?: number;
  /** The section lays out its own padding. */
  flush?: boolean;
  content: ReactNode;
};

/** The composer's one settings menu: sections on the left, the open section on the right, tabs across the top on phones. */
export function ComposerSettings({ trigger, sections, label, footer }: { trigger: ReactElement; sections: readonly SettingsSection[]; label: string; footer?: ReactNode }) {
  const [selected, setSelected] = useState(sections[0]?.id);
  const tabs = useRef(new Map<string, HTMLButtonElement>());
  // Sections come and go with the workflow: fall back to the first when the open one leaves.
  const active = sections.find((section) => section.id === selected) ?? sections[0];

  const move = (from: number, step: number) => {
    const next = sections[(from + step + sections.length) % sections.length];
    setSelected(next.id);
    tabs.current.get(next.id)?.focus();
  };

  return (
    <Popover>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        align="center"
        collisionPadding={12}
        className="flex max-h-[var(--radix-popover-content-available-height)] w-[min(94vw,720px)] flex-col overflow-hidden rounded-2xl p-0 sm:h-[460px] sm:flex-row"
      >
        <div className="flex min-h-0 shrink-0 flex-col border-b sm:w-44 sm:border-b-0 sm:border-r">
        <div role="tablist" aria-label={label} aria-orientation="vertical" className="flex min-h-0 flex-1 gap-1 overflow-x-auto p-2 sm:flex-col sm:overflow-y-auto">
          {sections.map((section, index) => {
            const isActive = section.id === active?.id;
            return (
              <button
                key={section.id}
                ref={(node) => { if (node) tabs.current.set(section.id, node); else tabs.current.delete(section.id); }}
                type="button"
                role="tab"
                id={`composer-settings-tab-${section.id}`}
                aria-selected={isActive}
                aria-controls="composer-settings-panel"
                tabIndex={isActive ? 0 : -1}
                onClick={() => setSelected(section.id)}
                onKeyDown={(event) => {
                  const step = event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 0;
                  if (step) { event.preventDefault(); move(index, step); }
                }}
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:w-full",
                  isActive && "bg-accent",
                )}
              >
                <span className="shrink-0 text-muted-foreground [&_svg]:size-4" aria-hidden>{section.icon}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{section.label}</span>
                  {section.value && <span className="hidden truncate text-xs text-muted-foreground sm:block">{section.value}</span>}
                </span>
                {Boolean(section.count) && <span className="shrink-0 rounded-full bg-foreground px-1.5 text-[10px] font-medium text-background">{section.count}</span>}
              </button>
            );
          })}
        </div>
        {footer && <div className="p-2 pt-0">{footer}</div>}
        </div>
        <div
          id="composer-settings-panel"
          role="tabpanel"
          aria-labelledby={active ? `composer-settings-tab-${active.id}` : undefined}
          className={cn("min-h-0 min-w-0 flex-1 overflow-y-auto", !active?.flush && "p-4")}
        >
          {active?.content}
        </div>
      </PopoverContent>
    </Popover>
  );
}
