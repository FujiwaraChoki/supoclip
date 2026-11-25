export async function trackTillySubscriptionStarted(
  visitorId: string | undefined,
  metadata: { plan: string; interval?: string; trial: boolean },
): Promise<void> {
  const baseUrl = process.env.TILLY_API_BASE_URL;
  const apiKey = process.env.TILLY_API_KEY;
  const websiteId = process.env.NEXT_PUBLIC_TILLY_WEBSITE_ID;
  if (!baseUrl || !apiKey || !websiteId || !visitorId || visitorId.length > 64) return;

  try {
    const endpoint = `${baseUrl.replace(/\/+$/, "")}/sites/${encodeURIComponent(websiteId)}/goals`;
    const response = await fetch(endpoint, {
      method: "POST",
      redirect: "error",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ visitorId, name: "subscription_started", metadata }),
      signal: AbortSignal.timeout(2_000),
    });
    if (!response.ok) console.warn("Tilly subscription goal rejected", response.status);
  } catch {
    console.warn("Tilly subscription goal request failed");
  }
}
