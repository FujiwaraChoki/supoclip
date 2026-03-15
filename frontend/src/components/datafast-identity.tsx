"use client";

import { useEffect, useRef } from "react";

import { useSession } from "@/lib/auth-client";
import { identify } from "@/lib/datafast";

export function DataFastIdentity() {
  const { data: session } = useSession();
  const lastPayloadRef = useRef<string | null>(null);

  useEffect(() => {
    const user = session?.user;

    if (!user?.id) {
      lastPayloadRef.current = null;
      return;
    }

    const payload = {
      user_id: user.id,
      ...(user.name ? { name: user.name } : {}),
      ...(user.image ? { image: user.image } : {}),
      is_admin: String(Boolean((user as { is_admin?: boolean }).is_admin)),
    };
    const serializedPayload = JSON.stringify(payload);

    if (serializedPayload === lastPayloadRef.current) {
      return;
    }

    identify(payload);
    lastPayloadRef.current = serializedPayload;
  }, [session?.user]);

  return null;
}
