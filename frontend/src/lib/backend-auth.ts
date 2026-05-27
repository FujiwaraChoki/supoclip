import crypto from "crypto";

export function getBackendAuthSecret(): string | null {
  const secret = process.env.BACKEND_AUTH_SECRET?.trim();
  return secret || null;
}

export function hasSignedBackendAuth(): boolean {
  return getBackendAuthSecret() !== null;
}

export function buildBackendAuthHeaders(userId: string): Record<string, string> {
  const secret = getBackendAuthSecret();
  if (!secret) {
    return { "x-supoclip-user-id": userId };
  }

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const payload = `${userId}:${timestamp}`;
  const signature = crypto.createHmac("sha256", secret).update(payload).digest("hex");

  return {
    "x-supoclip-user-id": userId,
    "x-supoclip-ts": timestamp,
    "x-supoclip-signature": signature,
  };
}
