import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ThemeProvider, useAppTheme } from "./theme-provider";
import { ThemeToggle } from "./theme-toggle";
import { TooltipProvider } from "@/components/ui/tooltip";

const navigation = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

let storage: Map<string, string>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => storage.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { storage.set(key, value); }),
    removeItem: vi.fn((key: string) => { storage.delete(key); }),
  });
  vi.stubGlobal("matchMedia", () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.className = "";
  navigation.pathname = "/";
});

function renderToggle(defaultTheme: "light" | "dark") {
  return render(
    <NextThemesProvider attribute="class" defaultTheme={defaultTheme} enableSystem={false}>
      <TooltipProvider>
        <ThemeToggle />
      </TooltipProvider>
    </NextThemesProvider>,
  );
}

it("switches from light to dark and back", async () => {
  const user = userEvent.setup();
  renderToggle("light");
  const toggle = screen.getByRole("button", { name: "Switch light/dark theme" });

  await user.click(toggle);
  expect(document.documentElement).toHaveClass("dark");
  expect(storage.get("theme")).toBe("dark");

  await user.click(toggle);
  expect(document.documentElement).toHaveClass("light");
  expect(storage.get("theme")).toBe("light");
});

it("switches to light when the app starts dark", async () => {
  const user = userEvent.setup();
  renderToggle("dark");

  await user.click(screen.getByRole("button", { name: "Switch light/dark theme" }));
  expect(document.documentElement).toHaveClass("light");
});

it("starts light even when the system prefers dark", () => {
  navigation.pathname = "/list";
  render(<ThemeProvider><span /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("light");
});

it("keeps marketing pages light after the app was switched to dark", () => {
  storage.set("theme", "dark");
  navigation.pathname = "/sign-in";
  render(<ThemeProvider><span /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("light");
  expect(document.documentElement).not.toHaveClass("dark");
});

it("uses the saved theme on app routes", () => {
  storage.set("theme", "dark");
  navigation.pathname = "/tasks/abc";
  render(<ThemeProvider><span /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("dark");
});

function SignedInShell() {
  useAppTheme(true);
  return null;
}

it("uses the saved theme on / once the signed-in app shell mounts", () => {
  storage.set("theme", "dark");
  const view = render(<ThemeProvider><span /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("light");

  view.rerender(<ThemeProvider><SignedInShell /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("dark");

  view.rerender(<ThemeProvider><span /></ThemeProvider>);
  expect(document.documentElement).toHaveClass("light");
});
