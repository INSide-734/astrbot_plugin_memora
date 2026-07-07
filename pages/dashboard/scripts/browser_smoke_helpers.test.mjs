import { describe, expect, it } from "vitest";
import { isRouteTextSettled } from "./browser_smoke_helpers.mjs";

describe("browser smoke helpers", () => {
  it("keeps route waits pending while expected text is present but loading text remains", () => {
    expect(isRouteTextSettled("知识图谱 加载中...", "知识图谱")).toBe(false);
    expect(isRouteTextSettled("Memory Loading...", "Memory")).toBe(false);
    expect(isRouteTextSettled("Граф Загрузка...", "Граф")).toBe(false);
  });

  it("allows route waits once every expected text is present and loading text is gone", () => {
    expect(isRouteTextSettled("系统概览 运行观测 Provider 状态", ["系统概览", "运行观测"])).toBe(true);
  });

  it("keeps route waits pending until every expected text is present", () => {
    expect(isRouteTextSettled("系统概览 Provider 状态", ["系统概览", "运行观测"])).toBe(false);
  });
});
