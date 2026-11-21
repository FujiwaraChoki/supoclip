import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import editor from "../../../messages/en/editor.json";
import examples from "../../../messages/en/examples.json";
import fixture from "@/test/fixtures/examples-manifest.json";
import { parseExamplesManifest } from "@/lib/examples";
import { ExamplesCatalog } from "./examples-catalog";

function renderCatalog() {
  const { sources } = parseExamplesManifest(fixture);
  render(
    <NextIntlClientProvider locale="en" messages={{ examples, editor }}>
      <ExamplesCatalog sources={sources} />
    </NextIntlClientProvider>,
  );
  return userEvent.setup();
}

describe("examples catalog", () => {
  it("groups clips by category and filters them", async () => {
    const user = renderCatalog();
    expect(screen.getByRole("heading", { name: "Podcasts & interviews" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Gaming & streams" })).toBeVisible();
    expect(screen.getAllByTestId("example-card")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Vlogs/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Gaming & streams/ }));
    expect(screen.getByRole("button", { name: /Gaming & streams/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("heading", { name: "Podcasts & interviews" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("example-card")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /^All/ }));
    expect(screen.getAllByTestId("example-card")).toHaveLength(3);
  });

  it("shows title, length, score, caption style and credits the creator", () => {
    renderCatalog();
    const card = screen.getAllByTestId("example-card")[0];
    expect(within(card).getByRole("heading", { name: "What one smuggled phone actually pays" })).toBeVisible();
    expect(within(card).getAllByText("0:37").length).toBeGreaterThan(0);
    expect(within(card).getByText("92")).toBeVisible();
    expect(within(card).getByText("Hormozi")).toBeVisible();
    expect(within(card).getByText("Tommy G")).toBeVisible();
    const original = within(card).getByRole("link", { name: /Watch the original/ });
    expect(original).toHaveAttribute("href", "https://www.youtube.com/watch?v=H9BSYGXS1XM");
    expect(original).toHaveAttribute("target", "_blank");
    expect(original).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("loads nothing until a clip is played, then plays it inline with controls", async () => {
    const user = renderCatalog();
    const card = screen.getAllByTestId("example-card")[0];
    expect(card.querySelector("video")).toBeNull();
    expect(card.querySelector("img")).toHaveAttribute("loading", "lazy");

    await user.click(within(card).getByRole("button", { name: "Play “What one smuggled phone actually pays”" }));
    const video = card.querySelector("video");
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute("preload", "none");
    expect(video).toHaveAttribute("playsinline");
    expect(video).toHaveAttribute("controls");
    expect(video).toHaveAttribute("src", "https://media.example.test/examples/v1/how-drugs-get-into-prison/clip-1.mp4");
  });
});
