import type { Mock, Procedure } from "@vitest/spy";

/** 为 TypeScript 7 下的无参数 vi.fn 保留可调用 mock 类型。 */
declare module "@vitest/spy" {
  function fn(): Mock<Procedure>;
}
