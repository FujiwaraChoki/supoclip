"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface IncompleteBatch {
  id: string;
  status: string;
}

/** On load, checks for a batch queue left mid-run from before an app/backend
 * restart (state is DB-backed, so it survives) and offers to resume it —
 * satisfies the restart-survival requirement without needing any
 * client-side persistence of its own. */
export function ResumeBatchPrompt() {
  const router = useRouter();
  const [batch, setBatch] = useState<IncompleteBatch | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    fetch("/api/batch-queue/incomplete", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { batch_queues: [] }))
      .then((data) => {
        const queues: IncompleteBatch[] = data.batch_queues || [];
        if (queues.length > 0) setBatch(queues[0]);
      })
      .catch(() => {});
  }, []);

  if (!batch || dismissed) return null;

  return (
    <AlertDialog open onOpenChange={(open) => !open && setDismissed(true)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Resume batch processing?</AlertDialogTitle>
          <AlertDialogDescription>
            A batch of videos was left {batch.status} before this session started. Would you like to
            resume it?
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => setDismissed(true)}>Not now</AlertDialogCancel>
          <AlertDialogAction onClick={() => router.push(`/batch/${batch.id}`)}>
            View batch
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
