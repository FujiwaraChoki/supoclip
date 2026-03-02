import crypto from "crypto";

export function buildBackendAuthHeaders(userId: string): Record<string, string> {
  const secret = process.env.BACKEND_AUTH_SECRET;

  // When secret is not set (e.g. self-hosted), send only user ID - backend accepts x-supoclip-user-id
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
