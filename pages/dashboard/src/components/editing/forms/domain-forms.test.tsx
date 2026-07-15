import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeForm } from "./KnowledgeForm";
import { MemoryForm } from "./MemoryForm";
import { NoteForm } from "./NoteForm";

vi.mock("@/hooks/useI18n", () => ({
  useI18n: () => ({ t: (key: string) => ({
    "field.content": "Content",
    "table.importance": "Importance",
    "table.type": "Type",
    "table.status": "Status",
    "field.title": "Title",
    "table.category": "Category",
    "table.confidence": "Confidence",
    "field.tags": "Tags",
    "category.fact": "Fact",
    "category.concept": "Concept",
    "category.rule": "Rule",
    "category.event": "Event",
    "category.procedure": "Procedure",
    "filter.statusActive": "Active",
    "filter.statusArchived": "Archived",
    "filter.statusDeleted": "Deleted",
    "edit.validationSummary": "Please correct the highlighted fields",
    "tags.remove": "Remove {0}",
  }[key] ?? key) }),
}));

describe("domain editing forms", () => {
  afterEach(cleanup);
  it("shows every editable memory field at once and connects validation", () => {
    render(
      <MemoryForm
        value={{ content: "Remember this", importance: 0.8, type: "fact", status: "active" }}
        onChange={vi.fn()}
        fieldErrors={{ content: "Content is required" }}
        mode="edit"
      />,
    );

    expect(screen.queryByLabelText("Choose field to edit")).toBeNull();
    expect(screen.getByLabelText("Content")).toBeTruthy();
    expect(screen.getByLabelText("Importance")).toBeTruthy();
    expect(screen.getByLabelText("Type")).toBeTruthy();
    expect(screen.getByLabelText("Status")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("Content is required");
  });

  it("shows all knowledge fields including controlled tags", () => {
    const onChange = vi.fn();
    render(
      <KnowledgeForm
        value={{ title: "Runbook", content: "Steps", category: "procedure", confidence: 0.8, tags: ["ops"] }}
        onChange={onChange}
        fieldErrors={{}}
        mode="edit"
      />,
    );

    expect(screen.queryByLabelText("Choose field to edit")).toBeNull();
    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByLabelText("Content")).toBeTruthy();
    expect(screen.getByLabelText("Category")).toBeTruthy();
    expect(screen.getByLabelText("Confidence")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Tags" }), { target: { value: "docs" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Tags" }), { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ tags: ["ops", "docs"] }));
  });

  it("uses the same note labels and validation in create and edit while hiding create status", () => {
    const value = { title: "Daily", content: "Summary", tags: ["work"], status: "active" };
    const { rerender } = render(
      <NoteForm value={value} onChange={vi.fn()} fieldErrors={{ title: "Title is required" }} mode="create" />,
    );

    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByLabelText("Content")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Tags" })).toBeTruthy();
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("Title is required");

    rerender(<NoteForm value={value} onChange={vi.fn()} fieldErrors={{}} mode="edit" />);
    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByLabelText("Content")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Tags" })).toBeTruthy();
    expect(screen.getByLabelText("Status")).toBeTruthy();
  });

  it("renders and links field errors for every domain form control", () => {
    const { rerender } = render(<MemoryForm value={{ content: "x", importance: 1, type: "fact", status: "active" }} onChange={vi.fn()} mode="edit" fieldErrors={{ content: "bad content", importance: "bad importance", type: "bad type", status: "bad status" }} />);
    for (const [label, message] of [["Content", "bad content"], ["Importance", "bad importance"], ["Type", "bad type"], ["Status", "bad status"]] as const) {
      const control = screen.getByLabelText(label);
      expect(control.getAttribute("aria-describedby")).toBeTruthy();
      expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain(message);
    }

    rerender(<KnowledgeForm value={{ title: "x", content: "x", category: "fact", confidence: 1, tags: [] }} onChange={vi.fn()} mode="edit" fieldErrors={{ title: "bad title", content: "bad content", category: "bad category", confidence: "bad confidence", tags: "bad tags" }} />);
    for (const [label, message] of [["Title", "bad title"], ["Content", "bad content"], ["Category", "bad category"], ["Confidence", "bad confidence"], ["Tags", "bad tags"]] as const) {
      const control = label === "Tags" ? screen.getByRole("textbox", { name: label }) : screen.getByLabelText(label);
      if (label !== "Tags") expect(control.getAttribute("aria-describedby")).toBeTruthy();
      expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain(message);
    }

    rerender(<NoteForm value={{ title: "x", content: "x", tags: [], status: "active" }} onChange={vi.fn()} mode="edit" fieldErrors={{ title: "bad title", content: "bad content", tags: "bad tags", status: "bad status" }} />);
    for (const [label, message] of [["Title", "bad title"], ["Content", "bad content"], ["Tags", "bad tags"], ["Status", "bad status"]] as const) {
      const control = label === "Tags" ? screen.getByRole("textbox", { name: label }) : screen.getByLabelText(label);
      if (label !== "Tags") expect(control.getAttribute("aria-describedby")).toBeTruthy();
      expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain(message);
    }
  });
});
