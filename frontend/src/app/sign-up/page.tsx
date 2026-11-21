import { SignUp } from "@/components/auth/sign-up";
import { AuthFrame } from "@/components/auth/auth-frame";
import { authPagePath } from "@/lib/auth-redirect";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; email?: string | string[] }>;
}) {
  const t = await getTranslations("auth");
  // Keep an invitee's return path and email when they switch between the two pages.
  const otherPage = authPagePath("/sign-in", await searchParams);
  return (
    <AuthFrame
      footer={<>{t("signUpPage.hasAccount")}{" "}<Link href={otherPage} className="font-medium text-foreground underline-offset-4 hover:underline">{t("signUpPage.signIn")}</Link></>}
    >
      <SignUp />
    </AuthFrame>
  );
}
