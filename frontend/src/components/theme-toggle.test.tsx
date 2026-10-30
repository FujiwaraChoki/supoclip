import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "next-themes";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ThemeToggle } from "./theme-toggle";
import { TooltipProvider } from "@/components/ui/tooltip";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.className = "";
  localStorage.clear();
});

function renderToggle(defaultTheme: "light" | "dark") {
  return render(
    <ThemeProvider attribute="class" defaultTheme={defaultTheme} enableSystem={false}>
      <TooltipProvider>
        <ThemeToggle />
      </TooltipProvider>
    </ThemeProvider>,
  );
}

it("switches from light to dark and back", async () => {
  const user = userEvent.setup();
  renderToggle("light");
  const toggle = screen.getByRole("button", { name: "Switch light/dark theme" });

  await user.click(toggle);
  expect(document.documentElement).toHaveClass("dark");
  expect(localStorage.getItem("theme")).toBe("dark");

  await user.click(toggle);
  expect(document.documentElement).toHaveClass("light");
  expect(localStorage.getItem("theme")).toBe("light");
});

it("switches to light when the app starts dark", async () => {
  const user = userEvent.setup();
  renderToggle("dark");

  await user.click(screen.getByRole("button", { name: "Switch light/dark theme" }));
  expect(document.documentElement).toHaveClass("light");
});
