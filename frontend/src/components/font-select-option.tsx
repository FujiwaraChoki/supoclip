"use client";

import { Trash2, Type } from "lucide-react";

import { SelectItem } from "@/components/ui/select";

export interface FontOption {
  name: string;
  display_name: string;
  scope: "system" | "user";
}

interface FontSelectOptionProps {
  font: FontOption;
  isDeleting: boolean;
  onDelete: (font: FontOption) => void;
}

export function FontSelectOption({
  font,
  isDeleting,
  onDelete,
}: FontSelectOptionProps) {
  return (
    <div className="flex items-center gap-1">
      <SelectItem className="min-w-0 flex-1" value={font.name}>
        <span className="flex min-w-0 items-center gap-2">
          <Type className="h-3 w-3" />
          <span className="truncate">{font.display_name}</span>
        </span>
      </SelectItem>
      {font.scope === "user" && (
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
