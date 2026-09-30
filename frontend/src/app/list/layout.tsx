import { AppShell } from "@/components/app/app-shell";
import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function ListLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
