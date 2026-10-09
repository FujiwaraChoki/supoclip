"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  CREATOR_OFFER_TEXT,
  captureCreatorCodeFromUrl,
  clearCreatorCode,
  lookupCreatorCode,
  storeCreatorCode,
} from "@/lib/creator-code";
import { cn } from "@/lib/utils";

/** "Have a creator code?" for new subscribers; the code is applied by startUpgrade. */
export function CreatorCodeField({ className }: { className?: string }) {
  const [applied, setApplied] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [value, setValue] = useState("");
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void captureCreatorCodeFromUrl().then(setApplied);
  }, []);

  const apply = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsChecking(true);
    setError(null);
    const code = await lookupCreatorCode(value);
    setIsChecking(false);
    if (!code) {
      setError("That code isn't valid.");
      return;
    }
    storeCreatorCode(code);
    setApplied(code);
    setIsOpen(false);
    setValue("");
  };

  if (applied) {
    return (
      <p className={cn("text-xs text-muted-foreground", className)}>
        Creator code <span className="font-mono font-medium text-foreground">{applied}</span>: {CREATOR_OFFER_TEXT}.{" "}
        <button
          type="button"
          className="underline underline-offset-2 hover:text-foreground"
          onClick={() => {
            clearCreatorCode();
            setApplied(null);
          }}
        >
          Remove
        </button>
      </p>
    );
  }

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className={cn("text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline", className)}
      >
        Have a creator code?
      </button>
    );
  }

  return (
    <form onSubmit={apply} className={cn("space-y-1.5", className)}>
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Creator code"
          aria-label="Creator code"
          maxLength={20}
          autoFocus
          className="h-8 font-mono text-sm uppercase"
        />
        <Button type="submit" size="sm" variant="outline" disabled={!value.trim() || isChecking}>
          {isChecking ? <Loader2 className="size-3.5 animate-spin" /> : "Apply"}
        </Button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </form>
  );
}
