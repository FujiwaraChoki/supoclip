import { render, screen, cleanup } from "@testing-library/react";
import StatusPage from "./page";

const { findMany } = vi.hoisted(() => ({ findMany: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ serviceStatus: { findMany } }) }));

const component = {
  id: "video-downloads", name: "Video downloads", status: "degraded",
  headline: "Investigating", description: "YouTube downloads may time out.",
  updatedAt: new Date("2026-10-13T10:00:00Z"),
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("reflects status row updates on the next request", async () => {
  findMany.mockResolvedValue([component]);
  const first = render(await StatusPage());
  expect(screen.getByText("Degraded")).toBeInTheDocument();
  first.unmount();
  findMany.mockResolvedValue([{ ...component, status: "operational", headline: "Resolved", description: "Downloads are working normally." }]);
  render(await StatusPage());
  expect(screen.getByText("Operational")).toBeInTheDocument();
  expect(screen.getByText("Resolved")).toBeInTheDocument();
  expect(screen.queryByText("Investigating")).not.toBeInTheDocument();
});

it("reports unavailable status when the database cannot be reached", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  findMany.mockRejectedValue(new Error("Database unavailable"));
  render(await StatusPage());
  expect(screen.getByRole("status")).toHaveTextContent("Service status is temporarily unavailable");
  expect(screen.queryByText("Operational")).not.toBeInTheDocument();
});
