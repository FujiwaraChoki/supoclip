/** Campaign image: an optional picture shown above the video on every clip of a project. */

export const CAMPAIGN_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const CAMPAIGN_IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp";
export const CAMPAIGN_IMAGE_MAX_BYTES = 8 * 1024 * 1024;

export type CampaignImageProblem = "type" | "size";

export function campaignImageProblem(file: Pick<File, "type" | "size">): CampaignImageProblem | null {
  if (!(CAMPAIGN_IMAGE_TYPES as readonly string[]).includes(file.type)) return "type";
  if (file.size > CAMPAIGN_IMAGE_MAX_BYTES) return "size";
  return null;
}

/** Uploads the image and returns the id to send as `campaign_image_id`. */
export async function uploadCampaignImage(file: File): Promise<string> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/campaign-images", { method: "POST", body });
  const payload = await response.json().catch(() => null);
  const id = payload?.campaign_image?.id;
  if (!response.ok || typeof id !== "string") {
    const detail = typeof payload?.detail === "string" ? payload.detail : null;
    throw new Error(detail ?? `Campaign image upload failed (${response.status})`);
  }
  return id;
}
