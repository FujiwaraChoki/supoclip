import { createSocialImageResponse } from "@/lib/social-image";

export const runtime = "edge";

export const alt = "SupoClip — Turn long videos into viral-ready shorts";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return createSocialImageResponse();
}
