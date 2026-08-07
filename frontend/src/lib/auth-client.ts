import { createAuthClient } from "better-auth/react";
import { useContext, createContext, ReactNode } from "react";

export const authClient = createAuthClient();

export const {
  signIn,
  signOut,
  signUp,
  useSession: useSessionOriginal,
} = authClient;

// Mock user for self-hosted usage without authentication
const MOCK_USER = {
  id: "local-user",
  name: "User",
  email: "user@localhost",
  image: null,
  emailVerified: true,
  createdAt: new Date("2024-01-01"),
  updatedAt: new Date("2024-01-01"),
};

// Wrapper hook that returns mock user when not authenticated
export function useSession() {
  const session = useSessionOriginal();

  // If loading, return pending state
  if (session?.isPending) {
    return session;
  }

  // If no real user, return mock user
  if (!session?.data?.user) {
    return {
      data: { user: MOCK_USER },
      isPending: false,
    };
  }

  return session;
}
