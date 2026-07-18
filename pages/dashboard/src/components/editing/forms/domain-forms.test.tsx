import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeForm } from "./KnowledgeForm";
import { MemoryForm } from "./MemoryForm";
import { NoteForm } from "./NoteForm";
import { ProfileForm } from "./ProfileForm";
import { SocialRelationForm } from "./SocialRelationForm";
import { AffectionForm } from "./AffectionForm";
import { JargonForm } from "./JargonForm";
import { MoodForm } from "./MoodForm";

vi.mock("@/hooks/useI18n", async () => {
  const { EN_MAP } = await import("@/mock");
  return {
    useI18n: () => ({
      t: (key: string, ...args: string[]) => {
        let value = key === "relation.colleague"
          ? "Workmate"
          : key === "status.active"
            ? "Currently active"
            : (EN_MAP[key] ?? key);
        args.forEach((arg, index) => {
          value = value.replace(new RegExp(`\\{${index}\\}`, "g"), () => arg);
        });
        return value;
      },
    }),
  };
});

describe("domain editing forms", () => {
  afterEach(cleanup);

  const profileValue = {
    user_id: "alice",
    display_name: "Alice",
    preferences: { reply_style: "concise", preferred_topics: ["ops"], avoided_topics: ["spoilers"], active_hours: [9, 17] },
    tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
  };

  const focusCases = [
    {
      name: "memory textarea",
      form: () => <MemoryForm value={{ content: "x", importance: 1, type: "fact", status: "active" }} onChange={vi.fn()} mode="edit" fieldErrors={{ content: "bad content" }} />,
      control: () => screen.getByLabelText("Content"),
    },
    {
      name: "knowledge select",
      form: () => <KnowledgeForm value={{ title: "x", content: "x", category: "fact", confidence: 1, tags: [] }} onChange={vi.fn()} mode="edit" fieldErrors={{ category: "bad category" }} />,
      control: () => screen.getByLabelText("Category"),
    },
    {
      name: "note tag editor",
      form: () => <NoteForm value={{ title: "x", content: "x", tags: [], status: "active" }} onChange={vi.fn()} mode="edit" fieldErrors={{ tags: "bad tags" }} />,
      control: () => screen.getByRole("textbox", { name: "Tags" }),
    },
    {
      name: "profile user id",
      form: () => <ProfileForm value={profileValue} onChange={vi.fn()} mode="create" fieldErrors={{ user_id: "bad user" }} />,
      control: () => screen.getByLabelText("User ID"),
    },
    {
      name: "social relation select",
      form: () => <SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "g1", relation_type: "colleague", strength: 0.5, tags: [] }} onChange={vi.fn()} mode="create" fieldErrors={{ relation_type: "bad relation" }} />,
      control: () => screen.getByLabelText("Relation type"),
    },
    {
      name: "jargon switch",
      form: () => <JargonForm value={{ term: "x", group_id: "g1", meaning: "meaning", confidence: 0.8, is_jargon: true, is_confirmed: false, is_global: false }} onChange={vi.fn()} mode="create" fieldErrors={{ is_confirmed: "bad confirmation" }} />,
      control: () => screen.getByRole("switch", { name: "Is confirmed" }),
    },
    {
      name: "affection number input",
      form: () => <AffectionForm value={{ user_id: "alice", group_id: "g1", affection_score: 10 }} onChange={vi.fn()} mode="create" fieldErrors={{ affection_score: "bad score" }} />,
      control: () => screen.getByLabelText("Affection score"),
    },
    {
      name: "mood textarea",
      form: () => <MoodForm value={{ group_id: "g1", mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "steady" }} onChange={vi.fn()} mode="create" fieldErrors={{ description: "bad description" }} />,
      control: () => screen.getByLabelText("Description"),
    },
  ];

  it.each(focusCases)("focuses the first invalid real control in the $name form", async ({ form, control }) => {
    render(form());

    await waitFor(() => expect(document.activeElement).toBe(control()));
  });

  const profileFocusCases = [
    ["preferences.reply_style", "Reply style selector"],
    ["preferences.preferred_topics", "Preferred topics"],
    ["preferences.avoided_topics", "Avoided topics"],
    ["preferences.active_hours.0", "Active hours start"],
    ["preferences.active_hours.1", "Active hours end"],
    ["tags.0.category", "Tag category"],
    ["tags.0.value", "Tag value"],
    ["tags.0.confidence", "Tag confidence"],
  ] as const;

  it.each(profileFocusCases)("focuses the Profile control registered for %s", async (field, label) => {
    render(<ProfileForm value={profileValue} onChange={vi.fn()} mode="create" fieldErrors={{ [field]: `${field} error` }} />);

    const control = label === "Preferred topics" || label === "Avoided topics"
      ? screen.getByRole("textbox", { name: label })
      : screen.getByLabelText(label);
    await waitFor(() => expect(document.activeElement).toBe(control));
  });

  it("focuses the first Profile tag row for a root tags error", async () => {
    const value = {
      ...profileValue,
      tags: [
        profileValue.tags[0],
        { category: "skill", value: "typescript", confidence: 0.9 },
      ],
    };

    render(<ProfileForm value={value} onChange={vi.fn()} mode="create" fieldErrors={{ tags: "bad tags" }} />);

    await waitFor(() => expect(document.activeElement).toBe(screen.getAllByLabelText("Tag category")[0]));
  });

  it("renders each Profile tag as a labeled responsive group", () => {
    const value = {
      ...profileValue,
      tags: [
        profileValue.tags[0],
        { category: "skill", value: "typescript", confidence: 0.9 },
        { category: "interest", value: "", confidence: 0.5 },
      ],
    };

    render(<ProfileForm value={value} onChange={vi.fn()} mode="edit" fieldErrors={{}} />);

    const groups = screen.getAllByRole("group", { name: /^Tag [123]$/ });
    expect(groups).toHaveLength(3);
    expect(within(groups[0]).getByText("Tag 1")).toBeTruthy();
    expect(within(groups[0]).getByLabelText("Tag category")).toHaveProperty("value", "interest");
    expect(within(groups[0]).getByLabelText("Tag value")).toHaveProperty("value", "testing");
    expect(within(groups[0]).getByLabelText("Tag confidence")).toHaveProperty("value", "0.8");
    expect(within(groups[1]).getByText("Tag 2")).toBeTruthy();
    expect(within(groups[1]).getByLabelText("Tag category 2")).toHaveProperty("value", "skill");
    expect(within(groups[1]).getByLabelText("Tag value 2")).toHaveProperty("value", "typescript");
    expect(within(groups[1]).getByLabelText("Tag confidence 2")).toHaveProperty("value", "0.9");
    expect(within(groups[2]).getByText("Tag 3")).toBeTruthy();
    expect(within(groups[2]).getByLabelText("Tag value 3")).toHaveProperty("value", "");
    expect(within(groups[2]).getByPlaceholderText("Enter a tag value")).toBeTruthy();
  });

  it("removes detached Profile tag controls from the focus registry", async () => {
    const value = {
      ...profileValue,
      tags: [
        profileValue.tags[0],
        { category: "skill", value: "typescript", confidence: 0.9 },
      ],
    };
    const error = { "tags.1.confidence": "bad confidence" };
    const { rerender } = render(<ProfileForm value={value} onChange={vi.fn()} mode="create" fieldErrors={error} />);
    const removedControl = screen.getByLabelText("Tag confidence 2");
    await waitFor(() => expect(document.activeElement).toBe(removedControl));

    rerender(<ProfileForm value={profileValue} onChange={vi.fn()} mode="create" fieldErrors={{}} />);
    rerender(<ProfileForm value={profileValue} onChange={vi.fn()} mode="create" fieldErrors={error} />);

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("alert")));
    expect(removedControl.isConnected).toBe(false);
  });

  function expectLinkedError(control: HTMLElement, message: string | RegExp) {
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    const href = screen.getByRole("link", { name: message }).getAttribute("href");
    expect(href).toMatch(/^#[A-Za-z0-9_-]+$/);
    const errorId = href!.slice(1);
    const describedBy = control.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(describedBy!.split(/\s+/)).toContain(errorId);
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
  }

  it("keeps jargon identity and stored context read-only while exposing editable flags", () => {
    const onChange = vi.fn();
    render(<JargonForm value={{ term: "yyds", group_id: "g1", meaning: "永远的神", confidence: 0.8, is_jargon: true, is_confirmed: false, is_global: false, is_complete: true, count: 3, last_inference_count: 2, created_at: 1, updated_at: 2, context_examples: ["这个方案 yyds"] }} onChange={onChange} fieldErrors={{}} mode="edit" />);
    expect(screen.getByLabelText("Term").hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Group ID").hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Meaning").hasAttribute("disabled")).toBe(false);
    expect(screen.getByText("这个方案 yyds")).toBeTruthy();
    expect(screen.queryByDisplayValue("这个方案 yyds")).toBeNull();
    expect(screen.getByRole("switch", { name: "Is jargon" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByText("Marks whether this term is jargon")).toBeTruthy();
  });

  it("validates jargon meaning and confidence boundaries accessibly", () => {
    const onChange = vi.fn();
    const { rerender } = render(<JargonForm value={{ term: "x", group_id: "g1", meaning: "", confidence: 0.5, is_jargon: false, is_confirmed: false, is_global: false }} onChange={onChange} fieldErrors={{ meaning: "Meaning is required" }} mode="create" />);
    const meaning = screen.getByLabelText("Meaning");
    expect(meaning.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("Meaning is required");
    rerender(<JargonForm value={{ term: "x", group_id: "g1", meaning: "ok", confidence: 1.2, is_jargon: false, is_confirmed: false, is_global: false }} onChange={onChange} fieldErrors={{}} mode="create" />);
    fireEvent.change(screen.getByLabelText("Confidence"), { target: { value: "2" } });
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("between 0 and 1");
    expectLinkedError(screen.getByLabelText("Confidence"), /between 0 and 1/);
    fireEvent.change(screen.getByLabelText("Confidence"), { target: { value: "1" } });
    expect(screen.queryByText("Must be between 0 and 1")).toBeNull();
  });

  it("allows only editable affection identity in create and integer scores in range", () => {
    const onChange = vi.fn();
    const { rerender } = render(<AffectionForm value={{ group_id: "g1", user_id: "u1", affection_score: 0, affection_level: "neutral", level_name: "Neutral", interaction_count: 4, last_interaction: 10 }} onChange={onChange} fieldErrors={{}} mode="create" />);
    expect(screen.getByLabelText("User ID").hasAttribute("disabled")).toBe(false);
    expect(screen.getByLabelText("Group ID").hasAttribute("disabled")).toBe(false);
    fireEvent.change(screen.getByLabelText("Affection score"), { target: { value: "1.5" } });
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("integer");
    expectLinkedError(screen.getByLabelText("Affection score"), /integer/);
    fireEvent.change(screen.getByLabelText("Affection score"), { target: { value: "100" } });
    expect(screen.queryByText(/integer|between -100 and 100/)).toBeNull();
    rerender(<AffectionForm value={{ group_id: "g1", user_id: "u1", affection_score: 0, affection_level: "neutral", level_name: "Neutral", interaction_count: 4, last_interaction: 10 }} onChange={onChange} fieldErrors={{}} mode="edit" />);
    expect(screen.getByLabelText("User ID").hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Group ID").hasAttribute("disabled")).toBe(true);
    expect(screen.queryByDisplayValue("Neutral")).toBeNull();
    expect(screen.queryByLabelText("Interaction count")).toBeNull();
  });

  it("uses lowercase values for every mood option and validates backend duration limits", () => {
    const onChange = vi.fn();
    render(<MoodForm value={{ group_id: "g1", mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "steady", start_time: 1, is_active: true }} onChange={onChange} fieldErrors={{}} mode="create" />);
    fireEvent.click(screen.getByRole("combobox", { name: "Mood type" }));
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(10);
    expect(options.map((option) => option.getAttribute("data-value") ?? option.textContent?.toLowerCase())).toEqual(expect.arrayContaining(["happy", "sad", "excited", "calm", "angry", "anxious", "playful", "serious", "nostalgic", "curious"]));
    fireEvent.change(screen.getByLabelText("Intensity"), { target: { value: "0" } });
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("0.1");
    expectLinkedError(screen.getByLabelText("Intensity"), /0\.1/);
    fireEvent.change(screen.getByLabelText("Intensity"), { target: { value: "0.1" } });
    fireEvent.change(screen.getByLabelText("Duration (hours)"), { target: { value: "169" } });
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toContain("168");
    fireEvent.change(screen.getByLabelText("Duration (hours)"), { target: { value: "168" } });
    expect(screen.queryByText(/168\.0/)).toBeNull();
    expect(screen.queryByLabelText("Start time")).toBeNull();
  });

  it("shows the selected mood with the same localized label inside and outside the menu", () => {
    render(<MoodForm value={{ group_id: "g1", mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "steady" }} onChange={vi.fn()} fieldErrors={{}} mode="create" />);

    const trigger = screen.getByRole("combobox", { name: "Mood type" });
    const selectedLabel = trigger.querySelector("[data-slot='select-value']");
    expect(selectedLabel?.textContent).toBe("Happy");
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: "Happy" }).textContent).toBe(selectedLabel?.textContent);
  });

  it("shows the selected relation with the same localized label inside and outside the menu", () => {
    render(<SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "g1", relation_type: "colleague", strength: 0.5, tags: [] }} onChange={vi.fn()} fieldErrors={{}} mode="create" />);

    const trigger = screen.getByRole("combobox", { name: "Relation type" });
    const selectedLabel = trigger.querySelector("[data-slot='select-value']");
    expect(selectedLabel?.textContent).toBe("Workmate");
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: "Workmate" }).textContent).toBe(selectedLabel?.textContent);
  });

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

  it("shows the selected note status with the same label inside and outside the menu", () => {
    render(<NoteForm value={{ title: "Daily", content: "Summary", tags: [], status: "active" }} onChange={vi.fn()} fieldErrors={{}} mode="edit" />);

    const trigger = screen.getByRole("combobox", { name: "Status" });
    const selectedLabel = trigger.querySelector("[data-slot='select-value']");
    expect(selectedLabel?.textContent).toBe("Currently active");
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: "Currently active" }).textContent).toBe(selectedLabel?.textContent);
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

  it("uses the validation summary as the only live alert while keeping one inline description target", () => {
    render(
      <MemoryForm
        value={{ content: "x", importance: 1, type: "fact", status: "active" }}
        onChange={vi.fn()}
        mode="edit"
        fieldErrors={{ content: "bad content" }}
      />,
    );

    const content = screen.getByLabelText("Content");
    const errorId = content.getAttribute("aria-describedby");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(errorId).toBeTruthy();
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
  });

  it("links server validation errors to real controls in every remaining domain form", () => {
    const profile = {
      user_id: "alice",
      display_name: "Alice",
      preferences: { reply_style: "concise", preferred_topics: ["ops"], avoided_topics: ["spoilers"], active_hours: [9, 17] },
      tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
    };
    const { rerender } = render(<ProfileForm value={profile} onChange={vi.fn()} mode="create" fieldErrors={{
      user_id: "profile user error",
      display_name: "profile name error",
      "preferences.reply_style": "reply style error",
      "preferences.preferred_topics": "preferred topics error",
      "preferences.avoided_topics": "avoided topics error",
      "preferences.active_hours.0": "active start error",
      "preferences.active_hours.1": "active end error",
      "tags.0.category": "tag category error",
      "tags.0.value": "tag value error",
      "tags.0.confidence": "tag confidence error",
    }} />);
    for (const [label, message] of [
      ["User ID", "profile user error"],
      ["Name", "profile name error"],
      ["Reply style selector", "reply style error"],
      ["Preferred topics", "preferred topics error"],
      ["Avoided topics", "avoided topics error"],
      ["Active hours start", "active start error"],
      ["Active hours end", "active end error"],
      ["Tag category", "tag category error"],
      ["Tag value", "tag value error"],
      ["Tag confidence", "tag confidence error"],
    ] as const) expectLinkedError(label.endsWith("topics") ? screen.getByRole("textbox", { name: label }) : screen.getByLabelText(label), message);

    rerender(<SocialRelationForm value={{ from_user: "alice", to_user: "bob", group_id: "g1", relation_type: "colleague", strength: 0.5, tags: ["work"] }} onChange={vi.fn()} mode="create" fieldErrors={{ from_user: "from error", to_user: "to error", group_id: "social group error", relation_type: "relation error", strength: "strength error", tags: "social tags error" }} />);
    for (const [label, message] of [["From user", "from error"], ["To user", "to error"], ["Group ID", "social group error"], ["Relation type", "relation error"], ["Strength", "strength error"], ["Tags", "social tags error"]] as const) expectLinkedError(label === "Tags" ? screen.getByRole("textbox", { name: label }) : screen.getByLabelText(label), message);

    rerender(<JargonForm value={{ term: "yyds", group_id: "g1", meaning: "meaning", confidence: 0.8, is_jargon: true, is_confirmed: false, is_global: false }} onChange={vi.fn()} mode="create" fieldErrors={{ term: "term error", group_id: "jargon group error", meaning: "meaning error", confidence: "confidence error", is_jargon: "jargon flag error", is_confirmed: "confirmed flag error", is_global: "global flag error" }} />);
    for (const [label, message] of [["Term", "term error"], ["Group ID", "jargon group error"], ["Meaning", "meaning error"], ["Confidence", "confidence error"], ["Is jargon", "jargon flag error"], ["Is confirmed", "confirmed flag error"], ["Is global", "global flag error"]] as const) expectLinkedError(screen.getByLabelText(label), message);

    rerender(<AffectionForm value={{ user_id: "alice", group_id: "g1", affection_score: 10 }} onChange={vi.fn()} mode="create" fieldErrors={{ user_id: "affection user error", group_id: "affection group error", affection_score: "score error" }} />);
    for (const [label, message] of [["User ID", "affection user error"], ["Group ID", "affection group error"], ["Affection score", "score error"]] as const) expectLinkedError(screen.getByLabelText(label), message);

    rerender(<MoodForm value={{ group_id: "g1", mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "steady" }} onChange={vi.fn()} mode="create" fieldErrors={{ group_id: "mood group error", mood_type: "mood type error", intensity: "mood intensity error", duration_hours: "mood duration error", description: "mood description error" }} />);
    for (const [label, message] of [["Group ID", "mood group error"], ["Mood type", "mood type error"], ["Intensity", "mood intensity error"], ["Duration (hours)", "mood duration error"], ["Description", "mood description error"]] as const) expectLinkedError(screen.getByLabelText(label), message);
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
    expectLinkedError(activeStart, /0.*23/);
    fireEvent.change(activeStart, { target: { value: "9" } });
    expect(profileChange).toHaveBeenCalledWith(expect.objectContaining({ preferences: expect.objectContaining({ active_hours: [9, 9] }) }));
    expect(activeStart.getAttribute("aria-invalid")).toBe("false");

    fireEvent.change(screen.getByLabelText("Tag confidence"), { target: { value: "1.2" } });
    const confidence = screen.getByLabelText("Tag confidence");
    expect(confidence.getAttribute("aria-invalid")).toBe("true");
    expect(confidence.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((node) => node.textContent).join(" ")).toMatch(/0.*1/);
    expectLinkedError(confidence, /0.*1/);
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
    expectLinkedError(strength, /0.*1/);
    fireEvent.change(strength, { target: { value: "0.8" } });
    expect(relationChange).toHaveBeenCalledWith(expect.objectContaining({ strength: 0.8 }));
    expect(strength.getAttribute("aria-invalid")).toBe("false");
  });
});
