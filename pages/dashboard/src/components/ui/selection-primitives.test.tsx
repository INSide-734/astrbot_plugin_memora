import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Checkbox } from "./checkbox";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./select";
import { Table, TableBody, TableCell, TableRow } from "./table";
import { Tabs, TabsList, TabsTrigger } from "./tabs";

afterEach(cleanup);

describe("selection-aware UI primitives", () => {
  it("renders default Tabs with a shallow active surface and transfers selection", () => {
    render(
      <Tabs defaultValue="active">
        <TabsList>
          <TabsTrigger value="active">Active</TabsTrigger>
          <TabsTrigger value="next">Next</TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    const active = screen.getByRole("tab", { name: "Active" });
    const next = screen.getByRole("tab", { name: "Next" });

    expect(active.hasAttribute("data-active")).toBe(true);
    expect(active.getAttribute("aria-selected")).toBe("true");
    expect(next.hasAttribute("data-active")).toBe(false);
    expect(next.getAttribute("aria-selected")).toBe("false");
    expect(active.className).toContain(
      "data-active:bg-[var(--selection-surface)]",
    );
    expect(active.className).toContain(
      "data-active:shadow-[inset_0_-2px_0_var(--selection-indicator)]",
    );
    expect(active.className).toContain("focus-visible:ring-[3px]");
    expect(active.className).toContain("motion-reduce:transition-none");

    next.focus();
    expect(document.activeElement).toBe(next);
    fireEvent.click(next);

    expect(active.hasAttribute("data-active")).toBe(false);
    expect(active.getAttribute("aria-selected")).toBe("false");
    expect(next.hasAttribute("data-active")).toBe(true);
    expect(next.getAttribute("aria-selected")).toBe("true");
  });

  it("renders line Tabs with a transparent active surface and theme indicator", () => {
    render(
      <Tabs defaultValue="first">
        <TabsList variant="line">
          <TabsTrigger value="first">First</TabsTrigger>
          <TabsTrigger value="second">Second</TabsTrigger>
          <TabsTrigger value="disabled" disabled>
            Disabled
          </TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    const first = screen.getByRole("tab", { name: "First" });
    const second = screen.getByRole("tab", { name: "Second" });
    const disabled = screen.getByRole("tab", { name: "Disabled" });

    expect(first.hasAttribute("data-active")).toBe(true);
    expect(first.className).toContain(
      "group-data-[variant=line]/tabs-list:data-active:bg-transparent",
    );
    expect(first.className).toContain(
      "group-data-[variant=line]/tabs-list:data-active:shadow-none",
    );
    expect(first.className).toContain(
      "group-data-[variant=line]/tabs-list:data-active:after:opacity-100",
    );
    expect(first.className).toContain(
      "after:bg-[var(--selection-indicator)]",
    );
    expect(second.hasAttribute("data-active")).toBe(false);

    fireEvent.click(second);

    expect(first.hasAttribute("data-active")).toBe(false);
    expect(first.getAttribute("aria-selected")).toBe("false");
    expect(second.hasAttribute("data-active")).toBe(true);
    expect(second.getAttribute("aria-selected")).toBe("true");
    expect(disabled.getAttribute("aria-disabled")).toBe("true");
    expect(disabled.className).toContain("aria-disabled:opacity-50");
  });

  it("moves SelectItem selection styling and Check indicator to the chosen option", async () => {
    const items = [
      { label: "One", value: "one" },
      { label: "Two", value: "two" },
    ];
    render(
      <Select items={items} defaultValue="one">
        <SelectTrigger aria-label="Choose value">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="one">One</SelectItem>
            <SelectItem value="two">Two</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole("combobox", { name: "Choose value" });
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);

    const initiallySelected = await screen.findByRole("option", { name: "One" });
    const nextOption = screen.getByRole("option", { name: "Two" });

    expect(initiallySelected.hasAttribute("data-selected")).toBe(true);
    expect(nextOption.hasAttribute("data-selected")).toBe(false);
    expect(initiallySelected.className).toContain(
      "data-[selected]:bg-[var(--selection-surface)]",
    );
    expect(initiallySelected.className).toContain(
      "data-[selected]:shadow-[inset_0_0_0_1px_var(--selection-border)]",
    );
    expect(initiallySelected.className.split(/\s+/)).not.toContain("border");
    expect(initiallySelected.className).toContain("data-[selected]:font-medium");
    expect(initiallySelected.className).toContain("focus:bg-accent");
    expect(initiallySelected.className).toContain("focus:ring-2");
    expect(initiallySelected.className).toContain("focus:ring-inset");
    expect(initiallySelected.className).toContain("focus:ring-ring/50");
    expect(initiallySelected.className).toContain(
      "data-[selected]:focus:**:text-[var(--selection-foreground)]",
    );
    expect(initiallySelected.className).toContain("motion-reduce:transition-none");
    expect(initiallySelected.querySelector("svg")).toBeTruthy();
    expect(nextOption.querySelector("svg")).toBeNull();

    initiallySelected.focus();
    expect(document.activeElement).toBe(initiallySelected);

    fireEvent.pointerDown(nextOption, { pointerType: "mouse" });
    fireEvent.click(nextOption);

    await waitFor(() => {
      expect(trigger.textContent).toContain("Two");
    });

    fireEvent.click(trigger);
    const formerlySelected = await screen.findByRole("option", { name: "One" });
    const newlySelected = screen.getByRole("option", { name: "Two" });

    expect(formerlySelected.hasAttribute("data-selected")).toBe(false);
    expect(formerlySelected.querySelector("svg")).toBeNull();
    expect(newlySelected.hasAttribute("data-selected")).toBe(true);
    expect(newlySelected.querySelector("svg")).toBeTruthy();
  });

  it("derives the open Select width from its trigger at runtime", async () => {
    const items = [
      { label: "Short", value: "short" },
      { label: "Long option", value: "long" },
    ];
    render(
      <Select items={items} defaultValue="short">
        <SelectTrigger aria-label="Adaptive width" className="w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>,
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Adaptive width" }));
    const content = (await screen.findByRole("listbox")).closest(
      "[data-slot='select-content']",
    );
    const classes = content?.className.split(/\s+/) ?? [];

    expect(classes).toContain("w-[var(--anchor-width)]");
    expect(classes).toContain("min-w-[var(--anchor-width)]");
    expect(classes).not.toContain("min-w-36");
  });

  it("toggles an enabled Checkbox, preserves disabled state, and exposes focus", () => {
    render(
      <>
        <Checkbox aria-label="Enabled item" />
        <Checkbox aria-label="Disabled item" defaultChecked disabled />
      </>,
    );

    const checkbox = screen.getByRole("checkbox", { name: "Enabled item" });
    const disabled = screen.getByRole("checkbox", { name: "Disabled item" });

    expect(checkbox.hasAttribute("data-checked")).toBe(false);
    expect(checkbox.className).toContain("data-checked:bg-primary");
    expect(checkbox.className).toContain("data-checked:text-primary-foreground");
    expect(checkbox.className).toContain(
      "transition-[color,background-color,border-color,box-shadow]",
    );
    expect(checkbox.className).toContain("duration-150");
    expect(checkbox.className).toContain("motion-reduce:transition-none");
    expect(checkbox.className).toContain("focus-visible:ring-3");
    expect(checkbox.className).toContain("disabled:opacity-50");

    checkbox.focus();
    expect(document.activeElement).toBe(checkbox);
    fireEvent.click(checkbox);
    expect(checkbox.hasAttribute("data-checked")).toBe(true);
    expect(checkbox.querySelector("svg")).toBeTruthy();
    fireEvent.click(checkbox);
    expect(checkbox.hasAttribute("data-checked")).toBe(false);
    expect(checkbox.querySelector("svg")).toBeNull();

    expect(disabled.hasAttribute("data-disabled")).toBe(true);
    expect(disabled.hasAttribute("data-checked")).toBe(true);
    fireEvent.click(disabled);
    expect(disabled.hasAttribute("data-checked")).toBe(true);
  });

  it("renders selected TableRow with an inset indicator that survives hover", () => {
    render(
      <Table>
        <TableBody>
          <TableRow data-state="selected">
            <TableCell>Selected memory</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    const row = screen.getByRole("row");

    expect(row.getAttribute("data-state")).toBe("selected");
    expect(row.className).toContain(
      "data-[state=selected]:bg-[var(--selection-surface)]",
    );
    expect(row.className).toContain(
      "data-[state=selected]:shadow-[inset_2px_0_0_var(--selection-indicator)]",
    );
    expect(row.className).toContain(
      "data-[state=selected]:hover:bg-[var(--selection-surface)]",
    );
    expect(row.className).toContain(
      "data-[state=selected]:has-aria-expanded:bg-[var(--selection-surface)]",
    );
    expect(row.className).toContain("motion-reduce:transition-none");
    expect(row.className).not.toContain("border-l-2");
  });
});
