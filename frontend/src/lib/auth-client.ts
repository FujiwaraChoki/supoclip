import { createAuthClient } from "better-auth/react";
import { useSyncExternalStore } from "react";

export const authClient = createAuthClient();

export const { signIn, signOut, signUp } = authClient;

const subscribeToHydration = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

export function useSession(...args: Parameters<typeof authClient.useSession>) {
  const session = authClient.useSession(...args);
  const hydrated = useSyncExternalStore(subscribeToHydration, clientSnapshot, serverSnapshot);

  // A sibling can resolve the shared auth store before this component hydrates.
  // Match the server's loading state until this consumer finishes hydration.
  return hydrated ? session : { ...session, data: null, isPending: true };
}
