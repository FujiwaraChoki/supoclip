import { SignIn } from "@/components/auth/sign-in";
import { AuthFrame } from "@/components/auth/auth-frame";
import { authPagePath } from "@/lib/auth-redirect";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; email?: string | string[] }>;
}) {
  const t = await getTranslations("auth");
  // Keep an invitee's return path and email when they switch between the two pages.
  const otherPage = authPagePath("/sign-up", await searchParams);
  return (
    <AuthFrame
      footer={<>{t("signInPage.noAccount")}{" "}<Link href={otherPage} className="font-medium text-foreground underline-offset-4 hover:underline">{t("signInPage.signUp")}</Link></>}
    >
      <SignIn />
    </AuthFrame>
  );
}
