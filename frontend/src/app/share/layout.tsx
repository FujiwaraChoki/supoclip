import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function ShareLayout({ children }: { children: React.ReactNode }) {
  return children;
}
