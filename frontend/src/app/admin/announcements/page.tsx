"use client";

import Link from "next/link";
import { AnnouncementManager } from "@/components/admin/announcement-manager";
import { useSession } from "@/lib/auth-client";

export default function AnnouncementsPage() {
  const { data: session, isPending } = useSession();
  if (isPending) return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
  if (!(session?.user as { is_admin?: boolean } | undefined)?.is_admin) {
    return <main className="p-8"><h1 className="font-display text-2xl font-bold">Announcements</h1><p className="mt-3 text-sm text-muted-foreground">Sign in with an admin account to manage announcements.</p></main>;
  }
  return (
    <main className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-8">
      <div>
        <Link href="/admin" className="text-sm text-muted-foreground hover:underline">Back to admin</Link>
        <h1 className="mt-3 font-display text-3xl font-bold tracking-tight">Announcements</h1>
      </div>
      <AnnouncementManager />
    </main>
  );
}
