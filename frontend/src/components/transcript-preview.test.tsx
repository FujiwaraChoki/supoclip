import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, vi } from "vitest"

import { TranscriptPreview } from "./transcript-preview"

afterEach(() => {
  vi.restoreAllMocks()
})

it("opens the full transcript when the preview is truncated", async () => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(96)
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(144)
  const user = userEvent.setup()

  render(<TranscriptPreview text="A long transcript that continues beyond the preview." clipTitle="Launch clip" />)

  await user.click(await screen.findByRole("button", { name: "See more" }))

  expect(screen.getByRole("dialog")).toBeInTheDocument()
  expect(screen.getByRole("heading", { name: "Full transcript" })).toBeInTheDocument()
  expect(screen.getByText("Transcript for Launch clip")).toBeInTheDocument()
})

it("does not show a modal action when the whole transcript fits", () => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(96)
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(96)

  render(<TranscriptPreview text="A short transcript." />)

  expect(screen.queryByRole("button", { name: "See more" })).not.toBeInTheDocument()
})
