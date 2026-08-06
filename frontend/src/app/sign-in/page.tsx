import { SignIn } from "@/components/auth/sign-in";
import Link from "next/link";

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8">
        <SignIn />
        <div className="text-center">
          <p className="text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link href="/sign-up" className="font-medium text-foreground underline underline-offset-4 hover:no-underline">
              Sign up
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
