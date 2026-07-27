import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function ListLayout({ children }: { children: React.ReactNode }) {
  return children;
}
