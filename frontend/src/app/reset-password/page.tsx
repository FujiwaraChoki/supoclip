import { Suspense } from "react";
import { ResetPassword } from "@/components/auth/reset-password";
import { AuthFrame } from "@/components/auth/auth-frame";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function ResetPasswordPage() {
  const t = await getTranslations("auth");
  return (
    <AuthFrame
      footer={<>{t("resetPage.back")}{" "}<Link href="/sign-in" className="font-medium text-foreground underline-offset-4 hover:underline">{t("resetPage.signIn")}</Link></>}
    >
      <Suspense>
        <ResetPassword />
      </Suspense>
    </AuthFrame>
  );
}
