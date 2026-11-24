"use client";

import { useQuery } from "@tanstack/react-query";

export interface CreatorApplication {
  slug: string | null;
  status: "pending" | "approved" | "declined" | "revoked";
  decline_reason: string | null;
  app_store_code_added_at: string | null;
  /** A payout country code, "ZZ" for elsewhere, or null for creators who applied before it was asked. */
  country: string | null;
}

export interface CreatorProgramState {
  application: CreatorApplication | null;
  /** When a declined applicant may apply again; null otherwise. */
  can_apply_at: string | null;
}

export const creatorProgramKeys = {
  all: ["creator-program"] as const,
  detail: (userId: string) => [...creatorProgramKeys.all, userId] as const,
};

export function useCreatorProgram(userId: string | undefined, loadError: string) {
  return useQuery({
    queryKey: creatorProgramKeys.detail(userId ?? ""),
    queryFn: async (): Promise<CreatorProgramState> => {
      const response = await fetch("/api/affiliates", { cache: "no-store" });
      if (!response.ok) throw new Error(loadError);
      const data = await response.json();
      return { application: data.application ?? null, can_apply_at: data.can_apply_at ?? null };
    },
    enabled: Boolean(userId),
  });
}
