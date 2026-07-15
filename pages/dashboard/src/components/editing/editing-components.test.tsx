import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeleteConfirmDialog } from "./DeleteConfirmDialog";
import { EditConflictDialog } from "./EditConflictDialog";
import { EditFormLayout } from "./EditFormLayout";
import { EntityCreateDialog } from "./EntityCreateDialog";
import { EntityEditorSheet } from "./EntityEditorSheet";
import { TagEditor } from "./TagEditor";
import { UnsavedChangesDialog } from "./UnsavedChangesDialog";
import { Button } from "@/components/ui/Button";

afterEach(cleanup);

const editorLabels = {
  edit: "Edit",
  close: "Close",
  cancel: "Cancel",
  save: "Save changes",
  saving: "Saving…",
};

function EditorHarness({
  submitting = false,
  canSave = true,
  initialMode = "view",
  onSave = vi.fn(),
}: {
  submitting?: boolean;
  canSave?: boolean;
  initialMode?: "view" | "edit";
  onSave?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(true);
  const [mode, setMode] = useState<"view" | "edit">(initialMode);
  const [draft, setDraft] = useState({ name: initialMode === "edit" ? "Changed" : "Old" });
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const isDirty = draft.name !== "Old";

  const requestOpen = (next: boolean) => {
    if (!next && isDirty) {
      setConfirmDiscard(true);
      return;
    }
    setOpen(next);
  };

  return (
    <>
      <EntityEditorSheet
        open={open}
        onOpenChange={requestOpen}
        title="Profile"
        description="Inspect and edit a profile."
        mode={mode}
        isDirty={isDirty}
        isSubmitting={submitting}
        canSave={canSave && isDirty}
        onBeginEdit={() => setMode("edit")}
        onCancel={() => {
          setDraft({ name: "Old" });
          setMode("view");
        }}
        onSave={onSave}
        view={<p>Old</p>}
        form={
          <label>
            Name
            <input
              aria-label="Name"
              value={draft.name}
              onChange={(event) => setDraft({ name: event.target.value })}
            />
          </label>
        }
        labels={editorLabels}
      />
      <UnsavedChangesDialog
        open={confirmDiscard}
        title="Discard unsaved changes?"
        description="Your changes will be lost."
        keepEditingLabel="Keep editing"
        discardLabel="Discard changes"
        onKeepEditing={() => setConfirmDiscard(false)}
        onDiscard={() => {
          setConfirmDiscard(false);
          setDraft({ name: "Old" });
          setOpen(false);
        }}
      />
    </>
  );
}

describe("EntityEditorSheet", () => {
  it("opens in view mode, then reveals the form and fixed footer when editing", () => {
    render(<EditorHarness />);

    expect(screen.getByText("Old")).toBeTruthy();
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByTestId("entity-editor-footer").className).toContain("border-t");
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("protects a dirty close and preserves or discards the draft only after the explicit choice", () => {
    render(<EditorHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(screen.getByRole("dialog", { name: "Discard unsaved changes?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByDisplayValue("Changed")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.queryByRole("dialog", { name: "Profile" })).toBeNull();
  });

  it("blocks duplicate saves and closing while submitting", () => {
    const onSave = vi.fn();
    render(<EditorHarness initialMode="edit" submitting onSave={onSave} />);

    expect(screen.getByRole("button", { name: "Saving…" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Close" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Profile" })).toBeTruthy();
  });

  it("blocks a second save while an asynchronous save request is pending", async () => {
    let resolveSave!: () => void;
    const onSave = vi.fn(() => new Promise<void>((resolve) => {
      resolveSave = resolve;
    }));
    render(<EditorHarness initialMode="edit" onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(onSave).toHaveBeenCalledOnce();

    resolveSave();
    await Promise.resolve();
    await Promise.resolve();
  });

  it("saves from Ctrl+Enter or Meta+Enter only when dirty, valid, and idle", async () => {
    const onSave = vi.fn();
    const { unmount } = render(<EditorHarness onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter", ctrlKey: true });
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Changed" } });
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter", ctrlKey: true });
    await Promise.resolve();
    await Promise.resolve();
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter", metaKey: true });
    expect(onSave).toHaveBeenCalledTimes(2);

    unmount();
    render(<EditorHarness canSave={false} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Changed" } });
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Delete", ctrlKey: true });
    expect(onSave).toHaveBeenCalledTimes(2);
  });
});

describe("EditFormLayout", () => {
  it("renders a focusable validation summary and focuses the first registered invalid control", () => {
    render(
      <EditFormLayout
        summaryLabel="Fix the following errors"
        fieldErrors={{ name: "Name is required", email: "Email is required" }}
        focusInvalid
      >
        {({ registerField, getFieldError }) => (
          <>
            <input aria-label="Name" ref={(element) => registerField("name", element)} aria-describedby={getFieldError("name")?.id} />
            <input aria-label="Email" ref={(element) => registerField("email", element)} aria-describedby={getFieldError("email")?.id} />
          </>
        )}
      </EditFormLayout>,
    );

    expect(screen.getAllByRole("alert")[0].getAttribute("tabindex")).toBe("-1");
    expect(screen.getAllByText("Name is required").find((element) => element.id)?.id).toContain("error-name");
    expect(document.activeElement).toBe(screen.getByLabelText("Name"));
  });

  it("keeps repeated form-level errors as separate summary entries", () => {
    render(
      <EditFormLayout summaryLabel="Fix the following errors" formErrors={["Save failed", "Save failed"]}>
        {() => <input aria-label="Name" />}
      </EditFormLayout>,
    );

    expect(screen.getAllByText("Save failed")).toHaveLength(2);
  });
});

describe("shared dialogs", () => {
  it("lets a conflict caller load remote values or reapply local values without saving", () => {
    const onLoadRemote = vi.fn();
    const onReapplyLocal = vi.fn();
    const onSave = vi.fn();
    render(
      <EditConflictDialog
        open
        title="Changes conflict"
        description="Someone else updated this record."
        loadRemoteLabel="Load remote values"
        reapplyLocalLabel="Reapply local values"
        onLoadRemote={onLoadRemote}
        onReapplyLocal={onReapplyLocal}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Load remote values" }));
    fireEvent.click(screen.getByRole("button", { name: "Reapply local values" }));
    expect(onLoadRemote).toHaveBeenCalledOnce();
    expect(onReapplyLocal).toHaveBeenCalledOnce();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("has an accessible destructive confirmation and requires an exact phrase only when requested", () => {
    const onConfirm = vi.fn();
    const { rerender } = render(
      <DeleteConfirmDialog
        open
        title="Delete memory?"
        description="This cannot be undone."
        cancelLabel="Cancel"
        confirmLabel="Delete memory"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Delete memory?", description: "This cannot be undone." });
    expect(dialog).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete memory" }));
    expect(onConfirm).toHaveBeenCalledOnce();

    rerender(
      <DeleteConfirmDialog
        open
        title="Delete 20 memories?"
        description="This cannot be undone."
        cancelLabel="Cancel"
        confirmLabel="Delete memories"
        confirmationRequirement={{ label: "Type DELETE", expectedText: "DELETE" }}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    const confirm = screen.getByRole("button", { name: "Delete memories" });
    expect(confirm).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Type DELETE"), { target: { value: "delete" } });
    expect(confirm).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Type DELETE"), { target: { value: "DELETE" } });
    expect(confirm).toHaveProperty("disabled", false);
  });

  it("requires a fresh exact confirmation phrase after a mounted dialog is closed and reopened", () => {
    function DeleteHarness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <Button type="button" onClick={() => setOpen(true)}>Reopen delete</Button>
          <DeleteConfirmDialog
            open={open}
            title="Delete memories?"
            description="This cannot be undone."
            cancelLabel="Cancel"
            confirmLabel="Delete memories"
            confirmationRequirement={{ label: "Type DELETE", expectedText: "DELETE" }}
            onCancel={() => setOpen(false)}
            onConfirm={vi.fn()}
          />
        </>
      );
    }

    render(<DeleteHarness />);
    fireEvent.change(screen.getByLabelText("Type DELETE"), { target: { value: "DELETE" } });
    expect(screen.getByRole("button", { name: "Delete memories" })).toHaveProperty("disabled", false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Delete memories?" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Reopen delete" }));
    expect(screen.getByRole("button", { name: "Delete memories" })).toHaveProperty("disabled", true);
  });
});

describe("EntityCreateDialog and TagEditor", () => {
  it("uses a mobile-scrollable create layout and delegates dirty close protection to its controlled owner", () => {
    function CreateHarness() {
      const [open, setOpen] = useState(true);
      const [value, setValue] = useState("");
      const [confirmDiscard, setConfirmDiscard] = useState(false);
      return (
        <>
          <EntityCreateDialog
            open={open}
            onOpenChange={(next) => {
              if (!next && value) setConfirmDiscard(true);
              else setOpen(next);
            }}
            title="Create note"
            description="Add a new note."
            isDirty={Boolean(value)}
            isSubmitting={false}
            canSubmit={Boolean(value)}
            onCancel={() => setOpen(false)}
            onSubmit={vi.fn()}
            form={<input aria-label="Title" value={value} onChange={(event) => setValue(event.target.value)} />}
            labels={{ close: "Close", cancel: "Cancel", submit: "Create", submitting: "Creating…" }}
          />
          <UnsavedChangesDialog
            open={confirmDiscard}
            title="Discard draft?"
            description="The draft will be lost."
            keepEditingLabel="Keep editing"
            discardLabel="Discard"
            onKeepEditing={() => setConfirmDiscard(false)}
            onDiscard={() => setOpen(false)}
          />
        </>
      );
    }

    render(<CreateHarness />);
    expect(screen.getByTestId("entity-create-content").className).toContain("overflow-y-auto");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog", { name: "Discard draft?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByDisplayValue("Draft")).toBeTruthy();
  });

  it("blocks a second create submission while an asynchronous request is pending", async () => {
    let resolveSubmit!: () => void;
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    }));
    render(
      <EntityCreateDialog
        open
        onOpenChange={vi.fn()}
        title="Create note"
        description="Add a new note."
        isDirty
        isSubmitting={false}
        canSubmit
        onCancel={vi.fn()}
        onSubmit={onSubmit}
        form={<input aria-label="Title" value="Draft" readOnly />}
        labels={{ close: "Close", cancel: "Cancel", submit: "Create", submitting: "Creating…" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onSubmit).toHaveBeenCalledOnce();

    resolveSubmit();
    await Promise.resolve();
    await Promise.resolve();
  });

  it("adds tags with Enter, removes the last with Backspace, filters duplicates, and reports its limit", () => {
    const onChange = vi.fn();
    const onLimitReached = vi.fn();
    function TagHarness() {
      const [values, setValues] = useState(["one"]);
      return (
        <TagEditor
          label="Tags"
          getRemoveLabel={(tag) => `Remove tag ${tag}`}
          values={values}
          onChange={(next) => {
            onChange(next);
            setValues(next);
          }}
          maxCount={2}
          onLimitReached={onLimitReached}
        />
      );
    }
    render(<TagHarness />);

    const input = screen.getByRole("textbox", { name: "Tags" });
    expect(screen.getByRole("button", { name: "Remove tag one" })).toBeTruthy();
    fireEvent.change(input, { target: { value: " two " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(["one", "two"]);

    fireEvent.change(input, { target: { value: "one" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: "three" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onLimitReached).toHaveBeenCalledOnce();

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(onChange).toHaveBeenLastCalledWith(["one"]);
  });
});
