import { render, screen } from "@testing-library/react";
import { VideoSourceIcon, videoSourceKind } from "./video-source-icon";

it("maps stored source types to their source", () => {
  expect(videoSourceKind("youtube")).toBe("youtube");
  expect(videoSourceKind("twitch")).toBe("twitch");
  expect(videoSourceKind("kick")).toBe("kick");
  expect(videoSourceKind("video_url")).toBe("upload");
  expect(videoSourceKind(undefined)).toBe("upload");
});

it.each([["twitch", "Twitch"], ["kick", "Kick"], ["youtube", "YouTube"]] as const)("labels the %s brand mark", (source, label) => {
  render(<VideoSourceIcon source={source} />);
  const icon = screen.getByRole("img", { name: label });
  expect(icon).toHaveAttribute("data-source", source);
  expect(icon).toHaveAttribute("fill", "currentColor");
});

it("hides decorative icons from assistive tech", () => {
  const { container } = render(<VideoSourceIcon source="kick" aria-hidden />);
  expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(screen.queryByRole("img")).toBeNull();
});
