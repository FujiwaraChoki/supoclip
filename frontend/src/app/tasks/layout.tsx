import { AppShell } from "@/components/app/app-shell";
import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function TasksLayout({ children }: { children: React.ReactNode }) {
  // A generation's results can start a tour (preview renders); the editor below it is immersive.
  return <AppShell tour={{ busy: false }}>{children}</AppShell>;
}
