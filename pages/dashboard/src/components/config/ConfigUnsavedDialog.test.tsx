import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigUnsavedDialog } from "./ConfigUnsavedDialog";

interface BridgeMock {
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

describe("ConfigUnsavedDialog", () => {
  beforeEach(() => {
    localStorage.clear();
    const bridge: BridgeMock = {
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    localStorage.clear();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("provides an accessible title and description", () => {
    render(
      <ConfigUnsavedDialog
        open
        onCancel={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", {
      name: "Leave configuration without saving?",
      description:
        "Your unsaved configuration changes will be lost if you leave this page.",
    });
    expect(dialog).toBeTruthy();
  });

  it("runs only the explicit keep-editing or discard command", () => {
    const onCancel = vi.fn();
    const onDiscard = vi.fn();
    render(
      <ConfigUnsavedDialog
        open
        onCancel={onCancel}
        onDiscard={onDiscard}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDiscard).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: "Discard changes and leave" }),
    );
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("maps Escape, backdrop, and close dismissal to cancel without discarding", () => {
    const onCancel = vi.fn();
    const onDiscard = vi.fn();
    render(
      <ConfigUnsavedDialog
        open
        onCancel={onCancel}
        onDiscard={onDiscard}
      />,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    const overlay = document.querySelector("[data-slot='dialog-overlay']");
    expect(overlay).not.toBeNull();
    fireEvent.pointerDown(overlay as Element);
    fireEvent.click(overlay as Element);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("keeps long action labels wrappable and marks discard as destructive", () => {
    render(
      <ConfigUnsavedDialog
        open
        onCancel={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    const keepEditing = screen.getByRole("button", { name: "Keep editing" });
    const discard = screen.getByRole("button", {
      name: "Discard changes and leave",
    });
    const footer = document.querySelector("[data-slot='dialog-footer']");

    expect(footer?.className).toContain("sm:flex-wrap");
    expect(keepEditing.className).toContain("whitespace-normal");
    expect(discard.className).toContain("whitespace-normal");
    expect(discard.className).toContain("text-destructive");
    expect(discard.getAttribute("type")).toBe("button");
  });
});
