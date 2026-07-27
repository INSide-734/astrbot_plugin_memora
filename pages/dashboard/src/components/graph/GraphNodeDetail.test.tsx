import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphNodeDetail } from "./GraphNodeDetail";
import type { GraphNode } from "@/types";

const translations: Record<string, string> = {
  "common.close": "关闭",
  "detail.nodeMemories": "关联记忆数",
  "detail.nodeDegree": "节点度数",
  "detail.nodeEntries": "关联条目数",
  "detail.nodeWeight": "权重",
  "table.type": "类型",
  "table.userId": "用户ID",
  "graph.nodePerson": "人物",
  "graph.nodeTopic": "主题",
  "graph.unnamedNode": "未命名节点",
};

afterEach(() => cleanup());

/** 返回组件测试使用的固定翻译。 */
function t(key: string): string {
  return translations[key] ?? key;
}

/** 构造带稳定 QQ 身份的人物节点。 */
function personNode(displayName: string): GraphNode {
  return {
    id: "7",
    label: displayName,
    display_name: displayName,
    type: "person",
    identity_namespace: "qq",
    stable_user_id: "10001",
    weight: 1.25,
    memory_count: 3,
    degree: 2,
    entry_count: 1,
  };
}

describe("GraphNodeDetail", () => {
  it("展示人物的最新昵称与稳定 QQ，并在改名后更新标题", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <GraphNodeDetail
        node={personNode("旧昵称")}
        locale="zh-CN"
        t={t}
        onClose={onClose}
      />,
    );

    expect(screen.getByRole("heading", { name: "旧昵称" })).toBeTruthy();
    expect(screen.getByText("QQ")).toBeTruthy();
    expect(screen.getByText("10001")).toBeTruthy();

    rerender(
      <GraphNodeDetail
        node={personNode("新昵称")}
        locale="zh-CN"
        t={t}
        onClose={onClose}
      />,
    );

    expect(screen.getByRole("heading", { name: "新昵称" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "旧昵称" })).toBeNull();
    expect(screen.getByText("10001")).toBeTruthy();
  });

  it("非 QQ 协议使用通用用户 ID，非人物节点不泄露身份字段", () => {
    const { rerender } = render(
      <GraphNodeDetail
        node={{
          ...personNode("其他协议用户"),
          identity_namespace: "future",
          stable_user_id: "member-7",
        }}
        locale="zh-CN"
        t={t}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByText("用户ID")).toBeTruthy();
    expect(screen.getByText("member-7")).toBeTruthy();
    expect(screen.queryByText("QQ")).toBeNull();

    rerender(
      <GraphNodeDetail
        node={{
          ...personNode("主题"),
          type: "topic",
        }}
        locale="zh-CN"
        t={t}
        onClose={() => undefined}
      />,
    );

    expect(screen.queryByText("10001")).toBeNull();
    expect(screen.queryByText("用户ID")).toBeNull();
  });

  it("关闭按钮具有可访问名称并触发回调", () => {
    const onClose = vi.fn();
    render(
      <GraphNodeDetail
        node={personNode("昵称")}
        locale="zh-CN"
        t={t}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
