"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Loader2, TriangleAlert } from "lucide-react";
import { authClient } from "@/lib/auth-client";
import { track } from "@/lib/datafast";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface DeleteAccountSectionProps {
  email: string;
  // An App Store subscription keeps billing until it's cancelled in the App Store.
  hasAppStoreSubscription?: boolean;
}

export function DeleteAccountSection({ email, hasAppStoreSubscription = false }: DeleteAccountSectionProps) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const confirmed = confirmation.trim().toLowerCase() === email.trim().toLowerCase();

  const handleOpenChange = (next: boolean) => {
    if (isDeleting) return;
    setOpen(next);
    if (!next) {
      setConfirmation("");
      setPassword("");
      setError(null);
    }
  };

  const handleDelete = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!confirmed || !password || isDeleting) return;

    setIsDeleting(true);
    setError(null);

    try {
      const response = await authClient.deleteUser({ password });
      if (response.error) {
        setError(response.error.message || "Failed to delete account");
        setIsDeleting(false);
        return;
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Failed to delete account");
      setIsDeleting(false);
      return;
    }

    track("account_deleted");
    // The server already revoked every session; this clears any client state.
    await authClient.signOut().catch(() => undefined);
    router.push("/");
    router.refresh();
  };

  return (
    <section className="rounded-2xl border border-red-200 bg-background dark:border-red-900/60">
      <div className="flex items-start gap-3 border-b border-red-200 px-5 py-4 dark:border-red-900/60">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-red-50 text-red-600 dark:bg-red-950/50">
          <TriangleAlert className="size-4" />
        </span>
        <div>
          <h2 className="text-sm font-semibold">Danger zone</h2>
          <p className="text-xs text-muted-foreground">Irreversible actions for your account.</p>
        </div>
      </div>
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-medium">Delete account</p>
          <p className="text-xs text-muted-foreground">
            Permanently delete your account, generations, clips and API keys.
          </p>
        </div>
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogTrigger asChild>
            <Button type="button" variant="destructive">Delete account</Button>
          </DialogTrigger>
          <DialogContent>
            <form onSubmit={handleDelete} className="space-y-4">
              <DialogHeader>
                <DialogTitle>Delete your account?</DialogTitle>
                <DialogDescription>
                  This permanently deletes your account, all generations and clips, and revokes your API keys. This
                  can&apos;t be undone.
                </DialogDescription>
              </DialogHeader>

              {hasAppStoreSubscription && (
                <Alert className="border-amber-200 bg-amber-50 dark:border-amber-900/60 dark:bg-amber-950/40">
                  <AlertCircle className="h-4 w-4 text-amber-600" />
                  <AlertDescription className="text-sm text-amber-800 dark:text-amber-200">
                    Your subscription is managed through the App Store. Deleting your account doesn&apos;t cancel
                    it, so cancel it in your App Store subscriptions to stop being charged.
                  </AlertDescription>
                </Alert>
              )}

              <div className="space-y-2">
                <Label htmlFor="delete-account-confirmation" className="text-sm font-normal">
                  Type <span className="font-semibold">{email}</span> to confirm
                </Label>
                <Input
                  id="delete-account-confirmation"
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                  autoComplete="off"
                  disabled={isDeleting}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="delete-account-password" className="text-sm font-normal">Password</Label>
                <Input
                  id="delete-account-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  disabled={isDeleting}
                />
              </div>

              {error && (
                <Alert className="border-red-200 bg-red-50">
                  <AlertCircle className="h-4 w-4 text-red-500" />
                  <AlertDescription className="text-sm text-red-700">{error}</AlertDescription>
                </Alert>
              )}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={isDeleting}>
                  Cancel
                </Button>
                <Button type="submit" variant="destructive" disabled={!confirmed || !password || isDeleting}>
                  {isDeleting ? <><Loader2 className="size-4 animate-spin" />Deleting...</> : "Delete account"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </section>
  );
}
