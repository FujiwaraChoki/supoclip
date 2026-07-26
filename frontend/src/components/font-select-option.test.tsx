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
});
