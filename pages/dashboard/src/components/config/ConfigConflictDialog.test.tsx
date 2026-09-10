import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfigConflictDialog } from "./ConfigConflictDialog";

const labels = {
  title: "Configuration changed in AstrBot",
  description:
    "Choose which version to continue editing. Reapplying does not save.",
  localChanges: "My local changes",
  remoteChanges: "AstrBot remote changes",
  overlapChanges: "Overlapping changes",
  loadRemote: "Load AstrBot version",
  reapplyLocal: "Reapply my changes on latest version",
  waitingRemote: "Waiting for the latest AstrBot configuration.",
  refreshRemote: "Refresh latest version",
};

const defaultProps = {
  open: true,
  localPaths: ["recall.top_k", "identity.bot_name"],
  remotePaths: ["recall.top_k", "provider.llm_provider_id"],
  overlapPaths: ["recall.top_k"],
  remoteReady: true,
  labels,
  onAcceptRemote: vi.fn(),
  onRebaseRemote: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConfigConflictDialog", () => {
  it("renders an accessible explanation and separate local, remote, and overlap path groups", () => {
    render(<ConfigConflictDialog {...defaultProps} />);

    const dialog = screen.getByRole("dialog", {
      name: "Configuration changed in AstrBot",
    });
    expect(
      within(dialog).getByText(
        "Choose which version to continue editing. Reapplying does not save.",
      ),
    ).toBeTruthy();

    const localGroup = within(dialog).getByRole("region", {
      name: "My local changes",
    });
    expect(within(localGroup).getByText("recall.top_k")).toBeTruthy();
    expect(within(localGroup).getByText("identity.bot_name")).toBeTruthy();

    const remoteGroup = within(dialog).getByRole("region", {
      name: "AstrBot remote changes",
    });
    expect(within(remoteGroup).getByText("recall.top_k")).toBeTruthy();
    expect(
      within(remoteGroup).getByText("provider.llm_provider_id"),
    ).toBeTruthy();

    const overlapGroup = within(dialog).getByRole("region", {
      name: "Overlapping changes",
    });
    expect(within(overlapGroup).getByText("recall.top_k")).toBeTruthy();
    expect(
      within(dialog).getByTestId("config-conflict-paths").className,
    ).toContain("overflow-y-auto");
  });

  it("runs the two explicit resolution commands", () => {
    const onAcceptRemote = vi.fn();
    const onRebaseRemote = vi.fn();
    render(
      <ConfigConflictDialog
        {...defaultProps}
        onAcceptRemote={onAcceptRemote}
        onRebaseRemote={onRebaseRemote}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Load AstrBot version" }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Reapply my changes on latest version",
      }),
    );

    expect(onAcceptRemote).toHaveBeenCalledTimes(1);
    expect(onRebaseRemote).toHaveBeenCalledTimes(1);
  });

  it("disables resolution until the full remote snapshot is ready and exposes refresh", () => {
    const onRefresh = vi.fn();
    render(
      <ConfigConflictDialog
        {...defaultProps}
        remoteReady={false}
        onRefresh={onRefresh}
      />,
    );

    expect(
      screen.getByText("Waiting for the latest AstrBot configuration."),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Load AstrBot version" }),
    ).toHaveProperty("disabled", true);
    expect(
      screen.getByRole("button", {
        name: "Reapply my changes on latest version",
      }),
    ).toHaveProperty("disabled", true);

    fireEvent.click(
      screen.getByRole("button", { name: "Refresh latest version" }),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("cannot be silently dismissed by Escape, backdrop, or a close button", () => {
    render(<ConfigConflictDialog {...defaultProps} />);

    fireEvent.keyDown(document, { key: "Escape" });
    const overlay = document.querySelector("[data-slot='dialog-overlay']");
    expect(overlay).not.toBeNull();
    fireEvent.pointerDown(overlay as Element);
    fireEvent.click(overlay as Element);

    expect(
      screen.getByRole("dialog", {
        name: "Configuration changed in AstrBot",
      }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /close/i })).toBeNull();
  });
});
