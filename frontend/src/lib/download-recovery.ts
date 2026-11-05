// The backend persists these public messages through the existing progress API.
// Only known recovery states receive this UI; provider errors stay in the logs.
const RECOVERY_MESSAGES = new Set([
  "Retrying video download with another connection...",
  "Recovering video audio...",
  "Recovering the full video download...",
  "Using a backup video download option...",
  "Trying another video download recovery option...",
]);

export const DELAY_MESSAGE_KEYS = ["snags", "hiccup", "scenicRoute", "bumps", "slower"] as const;

export function isDownloadRecovery(message: string) {
  return RECOVERY_MESSAGES.has(message);
}
