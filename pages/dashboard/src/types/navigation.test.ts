import { describe, expect, expectTypeOf, it } from "vitest";

import "./navigation";
import type {
  ConfigNavigationTarget,
  EntityNavigationTarget,
  PageNavigationIntent,
} from "./navigation";

describe("navigation types", () => {
  it("types config and repeatable entity navigation targets", () => {
    const target: ConfigNavigationTarget = {
      requestId: 7,
      path: "provider_settings.llm_provider_id",
      query: "LLM provider",
    };
    const intent: PageNavigationIntent = { configTarget: target };
    const entityTarget: EntityNavigationTarget = {
      requestId: 8,
      id: "memory-42",
    };
    const remoteResultIntent: PageNavigationIntent = { entityTarget };

    expect(intent).toEqual({ configTarget: target });
    expect(remoteResultIntent.entityTarget).toEqual(entityTarget);
    expectTypeOf(intent.configTarget).toMatchTypeOf<
      ConfigNavigationTarget | undefined
    >();
    expectTypeOf(remoteResultIntent.entityTarget).toMatchTypeOf<
      EntityNavigationTarget | undefined
    >();
  });
});
