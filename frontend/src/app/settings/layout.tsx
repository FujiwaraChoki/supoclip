import { AppHeader } from "@/components/app/app-header";
import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <><AppHeader />{children}</>;
}
