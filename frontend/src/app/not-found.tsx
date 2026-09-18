import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return <main className="mx-auto max-w-xl space-y-4 px-4 py-20 text-center">
    <h1 className="text-2xl font-semibold">Page not found</h1>
    <p className="text-muted-foreground">This page may have moved or been deleted.</p>
    <Button asChild><Link href="/">Back to SupoClip</Link></Button>
  </main>;
}
