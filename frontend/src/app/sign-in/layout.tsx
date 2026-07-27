import { noIndexFollowMetadata } from "@/lib/seo";

export const metadata = noIndexFollowMetadata;

export default function SignInLayout({ children }: { children: React.ReactNode }) {
  return children;
}
