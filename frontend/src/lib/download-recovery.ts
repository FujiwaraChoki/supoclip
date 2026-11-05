// The backend persists these public messages through the existing progress API.
// Only known recovery states receive this UI; provider errors stay in the logs.
const RECOVERY_MESSAGES = {
  "Retrying video download with another connection...": "connection",
  "Recovering video audio...": "audio",
  "Recovering the full video download...": "fullVideo",
  "Using a backup video download option...": "backup",
  "Trying another video download recovery option...": "anotherBackup",
} as const;

export function downloadRecoveryKey(message: string) {
  return Object.hasOwn(RECOVERY_MESSAGES, message)
    ? RECOVERY_MESSAGES[message as keyof typeof RECOVERY_MESSAGES]
    : null;
}
