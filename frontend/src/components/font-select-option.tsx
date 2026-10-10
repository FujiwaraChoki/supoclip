"use client";

import { Trash2, Type } from "lucide-react";
import { useRef } from "react";

import { SelectItem } from "@/components/ui/select";
import { FontLanguageInfo } from "@/components/font-language-info";

export interface FontOption {
  name: string;
  display_name: string;
  scope: "system" | "user";
  format?: string;
  supported_languages?: string[] | null;
}

interface FontSelectOptionProps {
  font: FontOption;
  isDeleting?: boolean;
  onDelete?: (font: FontOption) => void;
}

export function FontSelectOption({
  font,
  isDeleting,
  onDelete,
}: FontSelectOptionProps) {
  const itemRef = useRef<HTMLDivElement>(null);
  const infoRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="flex items-center gap-1">
      <SelectItem
        ref={itemRef}
        className="min-w-0 flex-1"
        value={font.name}
        aria-keyshortcuts="ArrowRight"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            event.preventDefault();
            event.stopPropagation();
            infoRef.current?.focus();
          }
        }}
      >
        <span className="flex min-w-0 items-center gap-2">
          <Type className="h-3 w-3" />
          <span className="truncate" style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>{font.display_name}</span>
        </span>
      </SelectItem>
      <FontLanguageInfo name={font.display_name} supportedLanguages={font.supported_languages} buttonRef={infoRef} onBack={() => itemRef.current?.focus()} />
      {font.scope === "user" && onDelete && (
        <button
          type="button"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-gray-500 hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:opacity-50"
          aria-label={`Delete ${font.display_name}`}
          title={`Delete ${font.display_name}`}
          disabled={isDeleting}
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onDelete(font);
          }}
        >
          <Trash2 className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
