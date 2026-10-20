import { SignIn } from "@/components/auth/sign-in";
import { AuthFrame } from "@/components/auth/auth-frame";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function SignInPage() {
  const t = await getTranslations("auth");
  return (
    <AuthFrame
      footer={<>{t("signInPage.noAccount")}{" "}<Link href="/sign-up" className="font-medium text-foreground underline-offset-4 hover:underline">{t("signInPage.signUp")}</Link></>}
    >
      <SignIn />
    </AuthFrame>
  );
}
