import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useConfigSync } from "@/hooks/useConfigSync";
import { ConfigPage } from "./ConfigPage";

vi.mock("@/hooks/useConfigSync", () => ({ useConfigSync: vi.fn() }));
vi.mock("@/hooks/useI18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

const useConfigSyncMock = vi.mocked(useConfigSync);

function syncResult(
  runtimeEffects: {
    manualRestartRequired: boolean;
    rebuildRequired: boolean;
  } | null,
): ReturnType<typeof useConfigSync> {
  return {
    schemaData: null,
    baseConfig: null,
    draft: null,
    revision: "rev-2",
    instanceId: "instance-1",
    remoteConfig: null,
    remoteRevision: null,
    remoteInstanceId: null,
    dirtyPaths: [],
    localPaths: [],
    remotePaths: [],
    overlapPaths: [],
    fieldErrors: {},
    status: "synced",
    error: null,
    runtimeEffects,
    promptDefaults: null,
    changeField: vi.fn(),
    refresh: vi.fn(),
    apply: vi.fn(),
    discardLocal: vi.fn(),
    acceptRemote: vi.fn(),
    rebaseRemote: vi.fn(),
  };
}

describe("ConfigPage 运行时生效提示", () => {
  beforeEach(() => {
    useConfigSyncMock.mockReset();
  });

  it("显示需要手动重启的持久提示", () => {
    useConfigSyncMock.mockReturnValue(
      syncResult({ manualRestartRequired: true, rebuildRequired: false }),
    );

    render(<ConfigPage />);

    expect(screen.getByText("config.restartRequiredTitle")).toBeTruthy();
    expect(
      screen.getByText("config.restartRequiredDescription"),
    ).toBeTruthy();
  });

  it("显示图派生数据需要重建的持久提示", () => {
    useConfigSyncMock.mockReturnValue(
      syncResult({ manualRestartRequired: false, rebuildRequired: true }),
    );

    render(<ConfigPage />);

    expect(screen.getByText("config.rebuildRequiredTitle")).toBeTruthy();
    expect(
      screen.getByText("config.rebuildRequiredDescription"),
    ).toBeTruthy();
  });
});
