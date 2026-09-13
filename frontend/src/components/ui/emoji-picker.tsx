"use client";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Curated static grid of common reaction emojis — no external dependency. */
export const REACTION_EMOJIS = [
  "😂", "😮", "😢", "🔥", "❤️", "👀", "💯", "😱",
  "🎉", "🤔", "👍", "👎", "😍", "🙌", "😭", "🤯",
  "👏", "💀", "😅", "🤩", "😎", "🥳", "😡", "🙄",
  "✨", "💪", "🤣", "😳", "🫡", "🚨", "🤝", "⚡",
];

interface EmojiPickerProps {
  onSelect: (emoji: string) => void;
  trigger?: React.ReactNode;
}

export function EmojiPicker({ onSelect, trigger }: EmojiPickerProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        {trigger ?? <Button variant="outline" size="sm">Pick emoji</Button>}
      </PopoverTrigger>
      <PopoverContent className="w-64">
        <div className="grid grid-cols-8 gap-1">
          {REACTION_EMOJIS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              onClick={() => onSelect(emoji)}
              className={cn(
                "text-xl leading-none p-1.5 hover:bg-accent hover:text-accent-foreground transition-colors",
              )}
              aria-label={`Add ${emoji} reaction`}
            >
              {emoji}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
