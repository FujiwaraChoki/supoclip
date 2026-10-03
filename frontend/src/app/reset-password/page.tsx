import { Suspense } from "react";
import { ResetPassword } from "@/components/auth/reset-password";
import { AuthFrame } from "@/components/auth/auth-frame";
import Link from "next/link";

export default function ResetPasswordPage() {
  return (
    <AuthFrame
      footer={<>Back to{" "}<Link href="/sign-in" className="font-medium text-foreground underline-offset-4 hover:underline">Sign in</Link></>}
    >
      <Suspense>
        <ResetPassword />
      </Suspense>
    </AuthFrame>
  );
}
