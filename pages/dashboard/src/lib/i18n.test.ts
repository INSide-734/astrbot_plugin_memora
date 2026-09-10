import { describe, expect, it, vi } from "vitest";

import {
  dashboardLocale,
  formatDashboardDate,
  formatDashboardDateTime,
  formatDashboardNumber,
  formatDashboardPercent,
  formatDashboardShortDate,
  translateEnum,
} from "./i18n";

describe("dashboard i18n helpers", () => {
  it("normalizes backend enum values before translation", () => {
    const t = vi.fn((key: string) => key === "memory.type.episodic" ? "情景记忆" : key);

    expect(translateEnum(t, "memory.type", "EPISODIC")).toBe("情景记忆");
    expect(t).toHaveBeenCalledWith("memory.type.episodic");
  });

  it("preserves unknown backend values and supports an explicit empty fallback", () => {
    const t = vi.fn((key: string) => key);

    expect(translateEnum(t, "runtime.status", "vendor_state")).toBe("vendor_state");
    expect(translateEnum(t, "runtime.status", "", "--")).toBe("--");
  });

  it("maps dashboard languages to Intl locales", () => {
    expect(dashboardLocale("zh")).toBe("zh-CN");
    expect(dashboardLocale("en-US")).toBe("en-US");
    expect(dashboardLocale("ru")).toBe("ru-RU");
    expect(dashboardLocale("unknown")).toBe("zh-CN");
  });

  it("formats ISO and Unix timestamps with the selected dashboard locale", () => {
    const iso = "2026-06-28T10:30:00Z";
    const seconds = 1782642600;

    expect(formatDashboardDate(iso, "en-US")).toBe(new Date(iso).toLocaleDateString("en-US"));
    expect(formatDashboardDateTime(seconds, "ru-RU")).toBe(
      new Date(seconds * 1000).toLocaleString("ru-RU"),
    );
    expect(formatDashboardDate("not-a-date", "zh-CN")).toBe("not-a-date");
    expect(formatDashboardDateTime("", "zh-CN")).toBe("");
  });

  it("formats date-only values in UTC so negative offsets cannot shift the calendar day", () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString").mockReturnValue("6/28/2026");

    expect(formatDashboardDate("2026-06-28", "en-US")).toBe("6/28/2026");
    expect(localeSpy).toHaveBeenCalledWith("en-US", { timeZone: "UTC" });
  });

  it("formats compact chart dates with the selected locale and UTC day", () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString").mockReturnValue("28.06");

    expect(formatDashboardShortDate("2026-06-28", "ru-RU")).toBe("28.06");
    expect(localeSpy).toHaveBeenCalledWith("ru-RU", {
      day: "numeric",
      month: "numeric",
      timeZone: "UTC",
    });
  });

  it("formats dashboard numbers with locale grouping, precision, and invalid-value fallback", () => {
    const options: Intl.NumberFormatOptions = {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    };

    expect(formatDashboardNumber(12345.6, "ru-RU", options)).toBe(
      new Intl.NumberFormat("ru-RU", options).format(12345.6),
    );
    expect(formatDashboardNumber("12345.6", "en-US", options)).toBe("12,345.6");
    expect(formatDashboardNumber("", "zh-CN", options)).toBe("--");
    expect(formatDashboardNumber("not-a-number", "zh-CN", options, "—")).toBe("—");
  });

  it("formats ratio values as locale-aware percentages", () => {
    const options: Intl.NumberFormatOptions = {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    };

    expect(formatDashboardPercent(0.125, "ru-RU", options)).toBe(
      new Intl.NumberFormat("ru-RU", { ...options, style: "percent" }).format(0.125),
    );
    expect(formatDashboardPercent(undefined, "en-US")).toBe("--");
  });
});
