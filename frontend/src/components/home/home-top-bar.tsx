"use client";

import Image from "next/image";
import Link from "next/link";
import { Bell, Activity, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import { toast } from "@/lib/toast";

/** Persistent, thin header — wordmark left, icon-only quick access right.
 * Deliberately no search input here (per product direction: this is an
 * operator dashboard, not a search surface). */
export function HomeTopBar() {
  return (
    <header className="border-b border-border bg-background">
      <div className="max-w-6xl mx-auto px-4 h-12 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Image src="/logo.png" alt="SupoClip" width={20} height={20} className="rounded-sm" />
          <span className="text-sm font-bold tracking-tight text-foreground">SupoClip</span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Notifications"
            onClick={() => toast.info("Notifications are coming soon")}
          >
            <Bell className="w-4 h-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="System status"
            onClick={() =>
              document.getElementById("system-status-strip")?.scrollIntoView({ behavior: "smooth" })
            }
          >
            <Activity className="w-4 h-4" />
          </Button>
          <Link href="/settings">
            <Button variant="ghost" size="icon-sm" aria-label="Settings">
              <Settings className="w-4 h-4" />
            </Button>
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
