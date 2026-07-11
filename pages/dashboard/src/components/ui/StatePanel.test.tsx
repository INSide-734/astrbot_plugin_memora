import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StatePanel } from "./StatePanel";

afterEach(cleanup);

describe("StatePanel", () => {
  it("renders an accessible empty state with an optional action", () => {
    const onAction = vi.fn();
    render(
      <StatePanel
        state="empty"
        title="No memories"
        description="Change filters or add a memory."
        actionLabel="Clear filters"
        onAction={onAction}
      />,
    );

    expect(screen.getByRole("status").getAttribute("data-state")).toBe("empty");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("announces errors and provides retry", () => {
    const onRetry = vi.fn();
    render(
      <StatePanel
        state="error"
        title="Unable to load"
        description="The request failed."
        actionLabel="Retry"
        onAction={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders deterministic loading placeholders", () => {
    const { container } = render(<StatePanel state="loading" title="Loading" />);
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3);
  });
});
