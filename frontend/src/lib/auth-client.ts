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
  createdAt: new Date(),
  updatedAt: new Date(),
};

// Wrapper hook that returns mock user when not authenticated
export function useSession() {
  const session = useSessionOriginal();

  // If no real session, return mock user
  if (!session?.data?.user) {
    return {
      data: session?.data ? { user: MOCK_USER } : null,
      isPending: session?.isPending ?? false,
    };
  }

  return session;
}
