import { fireEvent, render, screen } from "@testing-library/react";

import { FontSelectOption } from "./font-select-option";

vi.mock("@/components/ui/select", () => ({
  SelectItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe("FontSelectOption", () => {
  it("shows a delete action for a user-uploaded font", () => {
    const onDelete = vi.fn();
    const font = {
      name: "usr-user-1-brand-font",
      display_name: "Brand Font",
      scope: "user" as const,
    };

    render(<FontSelectOption font={font} isDeleting={false} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete Brand Font" }));

    expect(onDelete).toHaveBeenCalledWith(font);
  });

  it("does not offer deletion for bundled system fonts", () => {
    render(
      <FontSelectOption
        font={{ name: "Inter", display_name: "Inter", scope: "system" }}
        isDeleting={false}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("shows file coverage and fallback warnings for bundled and uploaded fonts", () => {
    render(<FontSelectOption font={{ name: "Inter", display_name: "Inter", scope: "system", supported_languages: ["en", "pt"] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Language support for Inter" }));

    expect(screen.getByText(/English, Portuguese/)).toBeInTheDocument();
    expect(screen.getByText(/Hindi/)).toBeInTheDocument();
    expect(screen.getByText(/Missing characters use a fallback font/)).toBeInTheDocument();
  });

  it("does not claim unsupported languages when coverage is unavailable", () => {
    render(<FontSelectOption font={{ name: "custom", display_name: "Custom", scope: "user", supported_languages: null }} />);
    fireEvent.click(screen.getByRole("button", { name: "Language support for Custom" }));

    expect(screen.getByText(/Language coverage could not be checked/)).toBeInTheDocument();
    expect(screen.queryByText(/Not fully supported/)).not.toBeInTheDocument();
  });
});
