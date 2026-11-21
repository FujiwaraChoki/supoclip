"use client";

// The Captions tab's template picker: the classic (ASS) templates as compact swatches, and the animated templates as
// live 9:16 tiles drawn by fx-core (the create screen's component). Animated templates are offered only when
// `/tasks/edit-styles` says so for this user; a draft that already uses one always keeps its own tile.
import { useTranslations } from "next-intl";

import { CaptionStyleTiles } from "@/components/caption-style-tiles";
import type { CaptionLook } from "@/lib/editor/document";
import { isFxTemplate, type FxCaptionTemplate } from "@/lib/fx/core/types";
import { cn } from "@/lib/utils";

/** The classic templates, in the order the create screen lists them (== caption_templates.CAPTION_TEMPLATES). */
export const CLASSIC_TEMPLATES = ["default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"] as const;

/** The classic template ids to offer: the session's looks when it sent them, else the built-in list. */
export function classicTemplates(looks: Record<string, CaptionLook> | undefined): string[] {
  const ids = Object.keys(looks ?? {}).filter((id) => !isFxTemplate(id));
  if (!ids.length) return [...CLASSIC_TEMPLATES];
  const known = CLASSIC_TEMPLATES.filter((id) => ids.includes(id));
  return [...known, ...ids.filter((id) => !(CLASSIC_TEMPLATES as readonly string[]).includes(id))];
}

/** Animated templates to show: the offered ones, plus the draft's own when it uses one that is not offered. */
export function animatedTemplates(offered: readonly FxCaptionTemplate[], current: string | undefined): FxCaptionTemplate[] {
  const ids = [...offered];
  if (isFxTemplate(current) && !ids.includes(current)) ids.push(current);
  return ids;
}

export function CaptionTemplatePicker({ value, looks, offered, onChange, disabled }: {
  /** The draft's template (`captions.template`); absent means "default". */
  value: string | undefined;
  looks?: Record<string, CaptionLook>;
  /** Animated templates the user may pick (`offeredFxTemplates(info)`); empty hides the group. */
  offered: readonly FxCaptionTemplate[];
  onChange: (template: string) => void;
  disabled?: boolean;
}) {
  const t = useTranslations("editor");
  const current = value || "default";
  const classic = classicTemplates(looks);
  const animated = animatedTemplates(offered, value);
  const name = (id: string) => (t.has(`captionTemplates.names.${id}`) ? t(`captionTemplates.names.${id}`) : id);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {animated.length > 0 && <p className="text-xs font-medium text-muted-foreground">{t("captionTemplates.classic")}</p>}
        <div role="radiogroup" aria-label={t("captionTemplates.classic")} className="grid grid-cols-4 gap-1.5">
          {classic.map((id) => {
            const look = looks?.[id];
            const checked = current === id;
            const accent = look?.highlightColor || look?.wordBoxColor || "#FACC15";
            return (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={checked}
                aria-label={name(id)}
                disabled={disabled}
                onClick={() => !checked && onChange(id)}
                className={cn(
                  "min-w-0 rounded-lg border p-1 text-left transition-colors outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50",
                  checked && "border-primary ring-1 ring-primary",
                )}
              >
                <span aria-hidden className="flex h-8 items-center justify-center rounded-md bg-stone-900 text-[11px] font-bold text-white">
                  <span
                    className={cn("rounded-sm px-1", look?.backgroundColor && "bg-black/60")}
                    style={{ WebkitTextStroke: look?.strokeWidth ? `0.5px ${look.strokeColor ?? "#000"}` : undefined }}
                  >
                    Aa <span style={{ color: accent }}>Bb</span>
                  </span>
                </span>
                <span className="block truncate px-0.5 pt-1 text-[11px] font-medium">{name(id)}</span>
              </button>
            );
          })}
        </div>
      </div>
      {animated.length > 0 && (
        <div className="space-y-2">
          <div className="space-y-0.5">
            <p className="text-xs font-medium text-muted-foreground">{t("captionTemplates.animated")}</p>
            <p className="text-xs leading-snug text-muted-foreground">{t("captionTemplates.animatedHint")}</p>
          </div>
          <CaptionStyleTiles
            options={animated.map((id) => ({ id }))}
            value={current}
            onChange={(id) => id !== current && onChange(id)}
            disabled={disabled}
          />
        </div>
      )}
    </div>
  );
}
