import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeForm } from "./KnowledgeForm";
import { MemoryForm } from "./MemoryForm";
import { NoteForm } from "./NoteForm";
import { ProfileForm } from "./ProfileForm";
import { SocialRelationForm } from "./SocialRelationForm";

vi.mock("@/hooks/useI18n", () => ({
  useI18n: () => ({ t: (key: string) => ({
    "field.content": "Content",
    "table.importance": "Importance",
    "table.type": "Type",
    "table.status": "Status",
    "field.title": "Title",
    "table.category": "Category",
    "table.confidence": "Confidence",
    "table.userId": "User ID",
    "table.name": "Name",
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
    "profile.replyStyle": "Reply style",
    "profile.preferredTopics": "Preferred topics",
    "profile.avoidedTopics": "Avoided topics",
    "profile.activeHours": "Active hours",
    "profile.activeHoursStart": "Active hours start",
    "profile.activeHoursEnd": "Active hours end",
    "profile.tagCategory": "Tag category",
    "profile.tagValue": "Tag value",
    "profile.tagConfidence": "Tag confidence",
    "profile.addTag": "Add tag",
    "profile.replyStyle.concise": "Concise",
    "profile.replyStyle.casual": "Casual",
    "profile.replyStyle.detailed": "Detailed",
    "social.fromUser": "From user",
    "social.toUser": "To user",
    "social.groupId": "Group ID",
    "social.relationType": "Relation type",
    "social.strength": "Strength",
    "relation.colleague": "Workmate",
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

  it("keeps a profile user ID editable only while creating and renders structured preferences", () => {
    const value = {
      user_id: "alice",
      display_name: "Alice",
      preferences: { reply_style: "concise", preferred_topics: ["ops"], avoided_topics: ["spoilers"], active_hours: [9, 17] },
      tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
    };
    const { rerender } = render(<ProfileForm value={value} onChange={vi.fn()} fieldErrors={{}} mode="create" />);
    expect(screen.getByLabelText("User ID")).toBeTruthy();
    expect(screen.getByLabelText("Reply style")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Preferred topics" })).toBeTruthy();
    expect(screen.queryByLabelText(/preferences json/i)).toBeNull();

    rerender(<ProfileForm value={value} onChange={vi.fn()} fieldErrors={{}} mode="edit" />);
    expect(screen.getByLabelText("User ID")).toHaveProperty("disabled", true);
  });

  it("uses the shared Select with localized relation labels and an unknown-value fallback", () => {
    const relationChange = vi.fn();
    const { rerender } = render(<SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "colleague", strength: 0.5, tags: [] }} onChange={relationChange} fieldErrors={{}} mode="edit" />);
    const relationType = screen.getByLabelText("Relation type");
    expect(relationType.tagName).toBe("BUTTON");
    expect(relationType.getAttribute("data-slot")).toBe("select-trigger");
    fireEvent.click(relationType);
    expect(screen.getByRole("option", { name: "Workmate" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "Workmate" }));
    expect(relationChange).toHaveBeenCalledWith(expect.objectContaining({ relation_type: "colleague" }));
    expect(screen.getByLabelText("From user")).toHaveProperty("disabled", true);

    rerender(<SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "future_relation", strength: 0.5, tags: [] }} onChange={relationChange} fieldErrors={{}} mode="edit" />);
    expect(screen.getByLabelText("Relation type").textContent).toContain("future_relation");
  });

  it("rejects out-of-range numeric input with accessible errors and clears them after valid correction", () => {
    const profileChange = vi.fn();
    render(<ProfileForm value={{ user_id: "alice", display_name: "Alice", preferences: { reply_style: "", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [{ category: "interest", value: "ops", confidence: 0.8 }] }} onChange={profileChange} fieldErrors={{}} mode="edit" />);
    const activeStart = screen.getByLabelText("Active hours start");
    fireEvent.change(activeStart, { target: { value: "24" } });
    expect(profileChange).not.toHaveBeenCalled();
    expect(activeStart.getAttribute("aria-invalid")).toBe("true");
    expect(activeStart.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toMatch(/0.*23/);
    fireEvent.change(activeStart, { target: { value: "9" } });
    expect(profileChange).toHaveBeenCalledWith(expect.objectContaining({ preferences: expect.objectContaining({ active_hours: [9, 9] }) }));
    expect(activeStart.getAttribute("aria-invalid")).toBe("false");

    fireEvent.change(screen.getByLabelText("Tag confidence"), { target: { value: "1.2" } });
    const confidence = screen.getByLabelText("Tag confidence");
    expect(confidence.getAttribute("aria-invalid")).toBe("true");
    expect(confidence.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toMatch(/0.*1/);
    fireEvent.change(confidence, { target: { value: "0.7" } });
    expect(profileChange).toHaveBeenLastCalledWith(expect.objectContaining({ tags: [{ category: "interest", value: "ops", confidence: 0.7 }] }));
    expect(confidence.getAttribute("aria-invalid")).toBe("false");

    const relationChange = vi.fn();
    render(<SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "colleague", strength: 0.5, tags: [] }} onChange={relationChange} fieldErrors={{}} mode="edit" />);
    const strength = screen.getByLabelText("Strength");
    fireEvent.change(strength, { target: { value: "1.1" } });
    expect(relationChange).not.toHaveBeenCalled();
    expect(strength.getAttribute("aria-invalid")).toBe("true");
    expect(strength.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toMatch(/0.*1/);
    fireEvent.change(strength, { target: { value: "0.8" } });
    expect(relationChange).toHaveBeenCalledWith(expect.objectContaining({ strength: 0.8 }));
    expect(strength.getAttribute("aria-invalid")).toBe("false");
  });
});
