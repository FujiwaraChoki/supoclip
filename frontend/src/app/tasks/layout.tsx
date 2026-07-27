import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

export default function TasksLayout({ children }: { children: React.ReactNode }) {
  return children;
}
