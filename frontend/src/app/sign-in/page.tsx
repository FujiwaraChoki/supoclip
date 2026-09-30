import { SignIn } from "@/components/auth/sign-in";
import { AuthFrame } from "@/components/auth/auth-frame";
import Link from "next/link";

export default function SignInPage() {
  return (
    <AuthFrame
      footer={<>Don&apos;t have an account?{" "}<Link href="/sign-up" className="font-medium text-foreground underline-offset-4 hover:underline">Sign up</Link></>}
    >
      <SignIn />
    </AuthFrame>
  );
}
