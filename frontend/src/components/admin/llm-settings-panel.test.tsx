import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { LlmSettingsPanel } from "./llm-settings-panel";

const settings = {
  primary: "google-gla:gemini-3-flash-preview",
  fallbacks: ["google-gla:gemini-2.5-flash"],
  chain: [
    "google-gla:gemini-3-flash-preview",
    "google-gla:gemini-2.5-flash",
  ],
  providers: ["google", "google-gla"],
  keys: {
    google: [{ fingerprint: "…4321·abcdef", source: "admin" }],
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/admin/llm") return Response.json(settings);
      if (url.endsWith("/chain") && init?.method === "PATCH") {
        return Response.json({ chain: settings.chain });
      }
      if (url.endsWith("/test/chain")) {
        return Response.json({
          ok: true,
          seconds: 3.2,
          answered_by: "google-gla:gemini-2.5-flash",
          attempts: [
            {
              model: "google-gla:gemini-3-flash-preview",
              provider: "google-gla",
              key_fingerprint: null,
              seconds: 1.1,
              ok: false,
              kind: "quota",
              error: "You exceeded your current quota",
            },
            {
              model: "google-gla:gemini-2.5-flash",
              provider: "google-gla",
              key_fingerprint: "…4321·abcdef",
              seconds: 2.1,
              ok: true,
              kind: null,
              error: "",
            },
          ],
        });
      }
      return Response.json({});
    }),
  );
});

it("shows the chain of models and the marks of the keys", async () => {
  render(<LlmSettingsPanel />);

  await waitFor(() =>
    expect(
      screen.getByText("google-gla:gemini-3-flash-preview"),
    ).toBeInTheDocument(),
  );
  expect(
    screen.getByText("google-gla:gemini-2.5-flash"),
  ).toBeInTheDocument();
  // The mark of a key is shown, the key itself never is.
  expect(screen.getByText("…4321·abcdef")).toBeInTheDocument();
  expect(screen.queryByText(/4321·abcdef/)).not.toBeNull();
});

it("saves the chain after a model was added", async () => {
  render(<LlmSettingsPanel />);
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  fireEvent.change(
    screen.getByPlaceholderText(/provider:model/i),
    { target: { value: "google-gla:gemini-2.5-flash-lite" } },
  );
  fireEvent.click(screen.getByRole("button", { name: /add a model/i }));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await waitFor(() => {
    const call = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .find((entry) => String(entry[0]).endsWith("/chain"));
    expect(call).toBeTruthy();
    expect(String((call?.[1] as { body: string }).body)).toContain(
      "gemini-2.5-flash-lite",
    );
  });
});

it("shows every attempt of the chain", async () => {
  render(<LlmSettingsPanel />);
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /run the chain/i }));

  await waitFor(() =>
    expect(
      screen.getByText("You exceeded your current quota"),
    ).toBeInTheDocument(),
  );
  expect(screen.getByText("Answered")).toBeInTheDocument();
  // The model of the chain and the model of the attempt are both listed.
  expect(
    screen.getAllByText("google-gla:gemini-2.5-flash").length,
  ).toBeGreaterThan(0);
});
