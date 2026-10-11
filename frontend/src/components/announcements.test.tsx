import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Announcements } from "./announcements";
import { http, HttpResponse } from "msw";
import { server } from "@/test/setup";

beforeEach(() => {
  server.use(http.get("/api/announcements", () => HttpResponse.json({ announcements: [{
    id: "creator-program-2026-10", title: "The creator program is open", description: "Free hosted Pro for approved creators.",
    publishedAt: "2026-10-09T00:00:00Z", href: "/affiliate", action: "Explore the creator program",
  }] })));
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("keeps the update accessible after reading and remembers it on another surface", async () => {
  const user = userEvent.setup();
  const view = render(<Announcements userId="read-test" />);
  await user.click(await screen.findByRole("button", { name: "What's new, 1 unread announcement" }));
  expect(screen.getByRole("heading", { name: "The creator program is open" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Explore the creator program" })).toHaveAttribute("href", "/affiliate");
  await user.click(screen.getByRole("button", { name: "Close announcements" }));
  expect(screen.getByRole("button", { name: "What's new" })).toBeVisible();
  view.unmount();
  render(<Announcements userId="read-test" compact />);
  await waitFor(() => expect(screen.getByRole("button", { name: "What's new" })).toBeVisible());
  expect(screen.queryByRole("button", { name: /unread announcement/ })).not.toBeInTheDocument();
});

it("shows new announcements even when an older update has been read", async () => {
  window.localStorage.setItem("supoclip:announcements-read:visitor", '["older-update"]');
  render(<Announcements landing />);
  expect(await screen.findByRole("button", { name: "What's new, 1 unread announcement" })).toBeVisible();
});

it("keeps read status separate when accounts change", async () => {
  const user = userEvent.setup();
  const view = render(<Announcements userId="first-account" />);
  await user.click(await screen.findByRole("button", { name: /1 unread announcement/ }));
  await user.keyboard("{Escape}");
  view.rerender(<Announcements userId="second-account" />);
  expect(await screen.findByRole("button", { name: /1 unread announcement/ })).toBeVisible();
});

it("clears the badge even when the browser refuses storage writes", async () => {
  vi.mocked(window.localStorage.setItem).mockImplementation(() => { throw new Error("Storage blocked"); });
  render(<Announcements userId="blocked-storage-test" />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /1 unread announcement/ }));
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "What's new" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /unread announcement/ })).not.toBeInTheDocument();
});
