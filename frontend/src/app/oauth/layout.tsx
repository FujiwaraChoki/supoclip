import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function OAuthLayout({ children }: { children: React.ReactNode }) {
  return children;
}
