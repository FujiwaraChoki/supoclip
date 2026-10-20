import { ForgotPassword } from "@/components/auth/forgot-password";
import { AuthFrame } from "@/components/auth/auth-frame";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function ForgotPasswordPage() {
  const t = await getTranslations("auth");
  return (
    <AuthFrame
      footer={<>{t("forgotPage.remembered")}{" "}<Link href="/sign-in" className="font-medium text-foreground underline-offset-4 hover:underline">{t("forgotPage.signIn")}</Link></>}
    >
      <ForgotPassword />
    </AuthFrame>
  );
}
