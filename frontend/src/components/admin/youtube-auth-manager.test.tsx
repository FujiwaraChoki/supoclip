import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { YouTubeAuthManager } from "./youtube-auth-manager";
import { server } from "@/test/setup";

describe("YouTubeAuthManager", () => {
  it("loads accounts from the admin API", async () => {
    server.use(
      http.get("*/api/admin/youtube-cookie-accounts", () =>
        HttpResponse.json({
          accounts: [
            {
              account: {
                id: "acc-1",
                label: "Primary account",
                email_hint: "team@example.com",
                status: "healthy",
                priority: 100,
                last_used_at: null,
                last_verified_at: null,
                consecutive_auth_failures: 0,
                last_error_code: null,
                last_error_message: null,
                cooldown_until: null,
              },
            },
          ],
        }),
      ),
    );

    render(<YouTubeAuthManager />);

    expect(await screen.findByText("Primary account")).toBeInTheDocument();
  });
});
