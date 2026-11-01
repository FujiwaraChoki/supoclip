"use client";

import { Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Flips between the light and dark theme. The icon is picked by the `dark`
 * class on <html>, not by React state, so the server render already matches.
 */
export function ThemeToggle({ className, tooltipSide = "top" }: { className?: string; tooltipSide?: "top" | "right" | "bottom" | "left" }) {
  const t = useTranslations("shell");
  const { resolvedTheme, setTheme } = useTheme();
  const label = t("theme.toggle");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          aria-label={label}
          className={cn("rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground", className)}
        >
          <Sun className="size-4 dark:hidden" />
          <Moon className="hidden size-4 dark:block" />
        </button>
      </TooltipTrigger>
      <TooltipContent side={tooltipSide}>{label}</TooltipContent>
    </Tooltip>
  );
}
