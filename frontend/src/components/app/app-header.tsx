"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { LogOut, Scissors } from "lucide-react";
import { toast } from "sonner";
import { signOut, useSession } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function AppHeader({ children }: { children?: React.ReactNode }) {
  const { data: session } = useSession();
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);
  if (!session?.user) return null;
  const admin = Boolean((session.user as { is_admin?: boolean }).is_admin);
  const links = [
    ["/", "Create"], ["/list", "Generations"], ["/settings", "Settings"],
    ...(admin ? [["/admin", "Admin"]] : []),
  ];
  return <header className="border-b border-border bg-background">
    <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4 sm:px-6">
      <Link href="/" className="flex items-center gap-2 font-[var(--font-syne)] text-xl font-bold tracking-tight">
        <Scissors className="size-5" aria-hidden />SupoClip
      </Link>
      <nav aria-label="Main navigation" className="order-3 flex w-full gap-1 sm:order-none sm:w-auto">
        {links.map(([href, label]) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href) || (href === "/list" && pathname.startsWith("/tasks"));
          return <Link key={href} href={href} aria-current={active ? "page" : undefined}
            className={cn("rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", active ? "bg-accent text-foreground" : "text-muted-foreground")}>{label}</Link>;
        })}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {children}
        <Link href="/settings" className="hidden max-w-40 truncate text-sm text-muted-foreground lg:block">{session.user.name || session.user.email}</Link>
        <Button variant="ghost" size="sm" disabled={signingOut} onClick={async () => {
          setSigningOut(true);
          try { await signOut(); window.location.assign("/sign-in"); }
          catch { toast.error("Could not sign out. Please try again."); setSigningOut(false); }
        }}><LogOut className="size-4" />{signingOut ? "Signing out…" : "Sign out"}</Button>
      </div>
    </div>
  </header>;
}
