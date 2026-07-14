import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigProviderOptions,
  ConfigSchemaNode,
  ConfigValue,
} from "@/types/config";

import { ConfigField } from "./ConfigField";

const providerOptions: ConfigProviderOptions = {
  llm: [
    { id: "llm-primary", label: "GPT Primary" },
    { id: "llm-backup", label: "GPT Backup" },
  ],
  embedding: [{ id: "embed-primary", label: "Embedding Primary" }],
};

interface RenderFieldOptions {
  path?: string;
  node: ConfigSchemaNode;
  value?: ConfigValue;
  onChange?: (path: string, value: ConfigValue) => void;
  disabled?: boolean;
  fieldErrors?: Record<string, string>;
  defaultProviderLabel?: string;
  targetPath?: string | null;
}

function renderField({
  path = "example.value",
  node,
  value,
  onChange = vi.fn(),
  disabled,
  fieldErrors,
  defaultProviderLabel,
  targetPath,
}: RenderFieldOptions) {
  render(
    <ConfigField
      path={path}
      node={node}
      value={value}
      onChange={onChange}
      providerOptions={providerOptions}
      disabled={disabled}
      fieldErrors={fieldErrors}
      defaultProviderLabel={defaultProviderLabel}
      targetPath={targetPath}
    />
  );
  return onChange;
}

afterEach(cleanup);

describe("ConfigField", () => {
  it("recursively renders object groups without rendering invisible children", () => {
    renderField({
      path: "provider_settings",
      node: {
        type: "object",
        description: "Provider settings",
        hint: "Select models used by memory processing.",
        items: {
          enabled: { type: "bool", description: "Provider enabled" },
          secret: {
            type: "string",
            description: "Hidden secret",
            invisible: true,
          },
        },
      },
      value: { enabled: true, secret: "hidden" },
    });

    const group = screen.getByRole("region", { name: "Provider settings" });
    expect(within(group).getByText("Select models used by memory processing.")).toBeTruthy();
    expect(within(group).getByText("provider_settings.enabled")).toBeTruthy();
    expect(within(group).getByRole("switch", { name: "Provider enabled" })).toBeTruthy();
    const groupHeading = group.querySelector("[data-slot='config-group-heading']");
    expect(groupHeading).toBeTruthy();
    expect(within(groupHeading as HTMLElement).getByRole("heading", {
      name: "Provider settings",
    })).toBeTruthy();
    expect(within(groupHeading as HTMLElement).getByText("provider_settings")).toBeTruthy();
    expect(groupHeading?.classList.contains("flex-wrap")).toBe(true);
    expect(groupHeading?.classList.contains("justify-between")).toBe(true);
    expect(
      within(groupHeading as HTMLElement)
        .getByText("provider_settings")
        .classList.contains("ml-auto"),
    ).toBe(true);
    expect(
      within(groupHeading as HTMLElement)
        .getByText("provider_settings")
        .classList.contains("text-right"),
    ).toBe(true);
    expect(screen.queryByText("Hidden secret")).toBeNull();
    expect(group.querySelector("[data-slot='card']")).toBeNull();
  });

  it("marks stable group and leaf paths and highlights the precise target", () => {
    renderField({
      path: "general",
      node: {
        type: "object",
        description: "General",
        items: {
          bot_name: { type: "string", description: "Bot name" },
        },
      },
      value: { bot_name: "Memora" },
      targetPath: "general.bot_name",
    });

    const group = screen.getByRole("region", { name: "General" });
    const leaf = screen
      .getByRole("textbox", { name: "Bot name" })
      .closest("[data-slot='field']");

    expect(group.getAttribute("data-config-path")).toBe("general");
    expect(leaf?.getAttribute("data-config-path")).toBe("general.bot_name");
    expect(leaf?.getAttribute("data-config-highlighted")).toBe("true");
  });

  it("emits booleans from a Switch", () => {
    const onChange = vi.fn();
    renderField({
      path: "recall.enabled",
      node: { type: "bool", description: "Recall enabled" },
      value: false,
      onChange,
    });

    fireEvent.click(screen.getByRole("switch", { name: "Recall enabled" }));

    expect(onChange).toHaveBeenCalledWith("recall.enabled", true);
  });

  it("keeps boolean content left and a visibly styled Switch right", () => {
    renderField({
      path: "session_manager.enable_full_group_capture",
      node: {
        type: "bool",
        description: "Capture all group messages",
        hint: "Capture every message in group chats.",
      },
      value: true,
    });

    const toggle = screen.getByRole("switch", {
      name: "Capture all group messages",
    });
    const field = toggle.closest("[data-slot='field']");
    const content = field?.querySelector("[data-slot='field-content']");

    expect(field?.getAttribute("data-orientation")).toBe("horizontal");
    expect(field?.classList.contains("justify-between")).toBe(true);
    expect(content?.classList.contains("min-w-0")).toBe(true);
    expect(toggle.classList.contains("data-[checked]:bg-primary")).toBe(true);
    expect(toggle.classList.contains("data-[unchecked]:bg-input")).toBe(true);
    expect(field?.children[1]).toBe(toggle);
  });

  it("renders schema options with the Base UI Select and emits the selected value", async () => {
    const onChange = vi.fn();
    renderField({
      path: "bot_language",
      node: {
        type: "string",
        description: "Bot language",
        options: ["zh", "en", "ru"],
      },
      value: "zh",
      onChange,
    });

    const trigger = screen.getByRole("combobox", { name: "Bot language" });
    fireEvent.click(trigger);
    const popup = await screen.findByRole("listbox");
    const content = popup.closest("[data-slot='select-content']");
    expect(content?.classList.contains("w-[var(--anchor-width)]")).toBe(true);
    expect(content?.classList.contains("min-w-[var(--anchor-width)]")).toBe(true);
    expect(content?.classList.contains("max-h-[var(--available-height)]")).toBe(true);
    expect(content?.parentElement?.getAttribute("data-align")).toBe("start");
    const englishOption = await screen.findByRole("option", { name: "en" });
    fireEvent.pointerDown(englishOption, { pointerType: "mouse" });
    fireEvent.click(englishOption);

    expect(onChange).toHaveBeenCalledWith("bot_language", "en");
  });

  it("emits strings from Input and preserves visible label, hint, and technical key", () => {
    const onChange = vi.fn();
    renderField({
      path: "identity.bot_name",
      node: {
        type: "string",
        description: "Bot name",
        hint: "Used in generated memories.",
      },
      value: "Memora",
      onChange,
    });

    const input = screen.getByRole("textbox", { name: "Bot name" });
    expect(input.getAttribute("id")).toMatch(/^config-[a-z0-9-]+$/);
    expect(screen.getByText("Used in generated memories.")).toBeTruthy();
    expect(screen.getByText("identity.bot_name")).toBeTruthy();

    const field = input.closest("[data-slot='field']");
    const heading = field?.querySelector("[data-slot='config-field-heading']");
    const key = screen.getByText("identity.bot_name");
    expect(heading).toBeTruthy();
    expect(heading?.classList.contains("flex-wrap")).toBe(true);
    expect(heading?.classList.contains("items-baseline")).toBe(true);
    expect(heading?.classList.contains("justify-between")).toBe(true);
    expect(key.parentElement).toBe(heading);
    expect(key.classList.contains("ml-auto")).toBe(true);
    expect(key.classList.contains("text-right")).toBe(true);

    fireEvent.change(input, { target: { value: "Archive" } });
    expect(onChange).toHaveBeenCalledWith("identity.bot_name", "Archive");
  });

  it("uses the final technical path segment when description is absent", () => {
    renderField({
      path: "identity.bot_name",
      node: { type: "string" },
      value: "Memora",
    });

    expect(screen.getByRole("textbox", { name: "bot_name" })).toBeTruthy();
  });

  it("uses Textarea for text schema nodes", () => {
    const onChange = vi.fn();
    renderField({
      path: "reflection.prompt",
      node: { type: "text", description: "Reflection prompt" },
      value: "Remember this",
      onChange,
    });

    const textarea = screen.getByRole("textbox", { name: "Reflection prompt" });
    expect(textarea.tagName).toBe("TEXTAREA");
    fireEvent.change(textarea, { target: { value: "Remember carefully" } });
    expect(onChange).toHaveBeenCalledWith(
      "reflection.prompt",
      "Remember carefully"
    );
  });

  it.each([
    ["int", "recall.top_k", "Top K", 8, "12", 12, "1"],
    ["float", "recall.threshold", "Threshold", 0.5, "0.75", 0.75, "0.05"],
  ] as const)(
    "emits a number for %s fields and forwards numeric constraints",
    (type, path, description, value, nextValue, emitted, step) => {
      const onChange = vi.fn();
      renderField({
        path,
        node: { type, description, min: 0, max: 100, step: Number(step) },
        value,
        onChange,
      });

      const input = screen.getByRole("spinbutton", { name: description });
      expect(input.getAttribute("min")).toBe("0");
      expect(input.getAttribute("max")).toBe("100");
      expect(input.getAttribute("step")).toBe(step);

      fireEvent.change(input, { target: { value: nextValue } });
      expect(onChange).toHaveBeenCalledWith(path, emitted);
      expect(typeof onChange.mock.calls[0][1]).toBe("number");
    }
  );

  it("populates LLM and embedding provider selectors with an empty default option", async () => {
    const llmChange = vi.fn();
    const { unmount } = render(
      <ConfigField
        path="provider_settings.llm_provider_id"
        node={{
          type: "string",
          description: "LLM provider",
          _special: "select_provider",
        }}
        value=""
        onChange={llmChange}
        providerOptions={providerOptions}
        defaultProviderLabel="Use framework default"
      />
    );

    fireEvent.click(screen.getByRole("combobox", { name: "LLM provider" }));
    expect(
      await screen.findByRole("option", { name: "Use framework default" })
    ).toBeTruthy();
    const llmOption = screen.getByRole("option", { name: "GPT Primary" });
    fireEvent.pointerDown(llmOption, { pointerType: "mouse" });
    fireEvent.click(llmOption);
    expect(llmChange).toHaveBeenCalledWith(
      "provider_settings.llm_provider_id",
      "llm-primary"
    );

    unmount();
    const embeddingChange = vi.fn();
    renderField({
      path: "provider_settings.embedding_provider_id",
      node: { type: "string", description: "Embedding provider" },
      value: "",
      onChange: embeddingChange,
    });

    fireEvent.click(
      screen.getByRole("combobox", { name: "Embedding provider" })
    );
    const embeddingOption = await screen.findByRole("option", {
      name: "Embedding Primary",
    });
    fireEvent.pointerDown(embeddingOption, { pointerType: "mouse" });
    fireEvent.click(embeddingOption);
    expect(embeddingChange).toHaveBeenCalledWith(
      "provider_settings.embedding_provider_id",
      "embed-primary"
    );
  });

  it("marks the Field and control invalid using path-indexed errors", () => {
    renderField({
      path: "recall.top_k",
      node: { type: "int", description: "Top K" },
      value: -1,
      fieldErrors: { "recall.top_k": "Must be positive" },
    });

    const input = screen.getByRole("spinbutton", { name: "Top K" });
    const field = input.closest("[data-slot='field']");
    expect(field?.hasAttribute("data-invalid")).toBe(true);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Must be positive")).toBeTruthy();
  });

  it("marks both the Field wrapper and its control disabled", () => {
    renderField({
      path: "identity.bot_name",
      node: { type: "string", description: "Bot name" },
      value: "Memora",
      disabled: true,
    });

    const input = screen.getByRole("textbox", { name: "Bot name" });
    const field = input.closest("[data-slot='field']");
    expect(field?.hasAttribute("data-disabled")).toBe(true);
    expect(input).toHaveProperty("disabled", true);
  });
});
