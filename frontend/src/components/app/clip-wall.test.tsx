import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import messages from "../../../messages/en/task.json";

import { ProcessingPanel } from "./clip-wall";

describe("download recovery progress", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a casual delay notice without an ETA, extra details, or increased progress", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { rerender } = render(<ProcessingPanel status="processing" progress={65}
      message="Using a backup video download option..." elapsedSeconds={90} />);

    expect(screen.getByText("Hang tight")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.snags);
    expect(screen.getByRole("status")).not.toHaveTextContent(/download|backup|connection|recovery/i);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "65");
    expect(screen.queryByText(/min left|minute left/)).not.toBeInTheDocument();

    rerender(<ProcessingPanel status="processing" progress={70}
      message="Creating clip 1/4..." elapsedSeconds={90} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Hang tight")).not.toBeInTheDocument();
    expect(screen.getByText("Less than a minute left")).toBeVisible();
  });

  it.each([
    "Retrying video download with another connection...",
    "Recovering video audio...",
    "Recovering the full video download...",
    "Trying another video download recovery option...",
  ])("shows a generic delay notice for persisted recovery state: %s", (message) => {
    render(<ProcessingPanel status="processing" progress={10} message={message} elapsedSeconds={30} />);
    expect(Object.values(messages.recovery.messages)).toContain(screen.getByRole("status").textContent);
  });

  it("chooses a random sentence once and keeps it through progress and recovery changes", () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0.4);
    const { rerender } = render(<ProcessingPanel status="processing" progress={10}
      message="Using a backup video download option..." elapsedSeconds={30} />);
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.scenicRoute);

    random.mockReturnValue(0.9);
    rerender(<ProcessingPanel status="processing" progress={65}
      message="Trying another video download recovery option..." elapsedSeconds={90} />);
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.scenicRoute);
  });

  it("does not expose unknown provider errors or stale recovery while queued", () => {
    const { rerender } = render(<ProcessingPanel status="processing" progress={10}
      message="private provider error" elapsedSeconds={30} />);
    expect(screen.queryByText("private provider error")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(<ProcessingPanel status="queued" progress={0}
      message="Using a backup video download option..." elapsedSeconds={30} />);
    expect(screen.getByText("Waiting for a worker")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
