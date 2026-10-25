"use client";

import { useCallback, useEffect } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

export type CopyOutcome = "copied" | "manual";

export type CopyTextOptions = {
  /** Element that already shows `text`. Selected when automatic copy fails. */
  target?: HTMLElement | null;
  /** Accessible name for the field shown when nothing on the page can be selected. */
  manualLabel?: string;
};

const MANUAL_COPY_FIELD_ID = "supoclip-manual-copy";

/**
 * Copy text, then fall back when the Clipboard API is missing or rejects
 * (permission denied, insecure context, iframe, or a browser without the API).
 * The returned promise never rejects: a failed legacy copy selects `target`
 * or shows the text so the user can copy it by hand.
 */
export async function copyText(text: string, options: CopyTextOptions = {}): Promise<CopyOutcome> {
  if (await clipboardCopy(text)) {
    removeManualCopyField();
    return "copied";
  }
  if (legacyCopy(text)) {
    removeManualCopyField();
    return "copied";
  }
  revealForManualCopy(text, options.target ?? null, options.manualLabel);
  return "manual";
}

export function useCopyText() {
  const t = useTranslations("common");
  useEffect(() => () => removeManualCopyField(), []);
  return useCallback(async (text: string, target?: HTMLElement | null) => {
    const outcome = await copyText(text, { target, manualLabel: t("copy.manual") });
    if (outcome === "copied") toast.success(t("copy.copied"));
    else toast.message(t("copy.manual"));
    return outcome;
  }, [t]);
}

async function clipboardCopy(text: string): Promise<boolean> {
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function legacyCopy(text: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") return false;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("aria-hidden", "true");
  textarea.tabIndex = -1;
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.width = "1px";
  textarea.style.height = "1px";
  textarea.style.padding = "0";
  textarea.style.border = "none";
  textarea.style.outline = "none";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

function revealForManualCopy(text: string, target: HTMLElement | null, manualLabel?: string) {
  if (target?.isConnected && selectElement(target)) {
    removeManualCopyField();
    return;
  }
  const existing = document.getElementById(MANUAL_COPY_FIELD_ID);
  const field = existing instanceof HTMLTextAreaElement ? existing : document.createElement("textarea");
  if (field !== existing) {
    existing?.remove();
    field.id = MANUAL_COPY_FIELD_ID;
    field.readOnly = true;
    field.rows = 3;
    field.style.position = "fixed";
    field.style.zIndex = "60";
    field.style.left = "50%";
    field.style.top = "1rem";
    field.style.transform = "translateX(-50%)";
    field.style.width = "min(32rem, calc(100vw - 2rem))";
    field.style.padding = "0.75rem";
    field.style.borderRadius = "0.75rem";
    field.style.border = "1px solid #e5e5e5";
    field.style.background = "#fff";
    field.style.color = "#171717";
    field.style.font = "14px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace";
    field.style.boxShadow = "0 10px 30px rgba(0, 0, 0, 0.15)";
    document.body.appendChild(field);
  }
  field.setAttribute("aria-label", manualLabel || "Copy this text");
  field.value = text;
  field.focus();
  field.select();
}

function selectElement(element: HTMLElement): boolean {
  try {
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      element.focus();
      element.select();
      const start = element.selectionStart ?? 0;
      const end = element.selectionEnd ?? 0;
      return document.activeElement === element && (element.value.length === 0 || end > start);
    }
    // Focusing a non-editable element collapses the highlight, which removes
    // the text the user needs to copy. Leave the range selected instead.
    const selection = window.getSelection?.();
    if (!selection) return false;
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    return selection.toString().length > 0;
  } catch {
    return false;
  }
}

function removeManualCopyField() {
  document.getElementById(MANUAL_COPY_FIELD_ID)?.remove();
}
