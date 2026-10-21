import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EVALUATION_EFFECT_GATE } from "@/mock/data";

import { EffectGateCard } from "./EffectGateCard";

describe("EffectGateCard", () => {
  afterEach(() => cleanup());

  it("renders readiness, five factor states and unmeasured metrics", () => {
    const { container } = render(<EffectGateCard gate={EVALUATION_EFFECT_GATE} />);

    expect(container.querySelector("[data-effect-gate-card]")).toBeTruthy();
    expect(screen.getByText("效果门")).toBeTruthy();
    expect(screen.getByText("可评估")).toBeTruthy();
    expect(screen.getAllByText("建议开启").length).toBeGreaterThan(0);
    expect(screen.getAllByText("建议关闭").length).toBeGreaterThan(0);
    expect(screen.getAllByText("拒绝").length).toBeGreaterThan(0);
    expect(screen.getAllByText("证据不足").length).toBeGreaterThan(0);
    expect(screen.getByText("95% CI")).toBeTruthy();
    expect(screen.getByText(/已检查 140 个值/)).toBeTruthy();
    expect(screen.getByText("dddddddddddd…")).toBeTruthy();
    expect(screen.getByText(/原子路由权重/)).toBeTruthy();
  });

  it("switches factor detail and shows category metrics", () => {
    render(<EffectGateCard gate={EVALUATION_EFFECT_GATE} />);

    fireEvent.click(screen.getByRole("button", { name: /图路/ }));
    expect(screen.getAllByText("关系").length).toBeGreaterThan(0);
    expect(screen.getByText(/graph_route_off/)).toBeTruthy();
    expect(screen.getByText("移除图路不劣于基线")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /最终重排/ }));
    expect(screen.getAllByText("未测量").length).toBeGreaterThan(1);
    expect(screen.getByText("缺少主指标")).toBeTruthy();
  });
});
