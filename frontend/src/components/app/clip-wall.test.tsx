import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProcessingPanel } from "./clip-wall";

describe("download recovery progress", () => {
  it("explains recovery without inventing an ETA or increasing progress", () => {
    const { rerender } = render(<ProcessingPanel status="processing" progress={65}
      message="Using a backup video download option..." elapsedSeconds={90} />);

    expect(screen.getByText("Recovering your video")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Using a backup download option");
    expect(screen.getByRole("status")).toHaveTextContent("This may take a few extra minutes");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "65");
    expect(screen.queryByText(/min left|minute left/)).not.toBeInTheDocument();

    rerender(<ProcessingPanel status="processing" progress={70}
      message="Creating clip 1/4..." elapsedSeconds={90} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Recovering your video")).not.toBeInTheDocument();
    expect(screen.getByText("Less than a minute left")).toBeVisible();
  });

  it.each([
    ["Retrying video download with another connection...", "Trying another connection"],
    ["Recovering video audio...", "another way to download"],
    ["Recovering the full video download...", "Downloading the full video"],
    ["Trying another video download recovery option...", "The previous option did not work"],
  ])("shows persisted recovery state on first render: %s", (message, text) => {
    render(<ProcessingPanel status="processing" progress={10} message={message} elapsedSeconds={30} />);
    expect(screen.getByRole("status")).toHaveTextContent(text);
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
