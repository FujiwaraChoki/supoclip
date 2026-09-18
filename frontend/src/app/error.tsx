"use client";

import { PageError } from "@/components/app/page-state";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return <PageError message="We couldn't load this page. Please try again." retry={reset} />;
}
