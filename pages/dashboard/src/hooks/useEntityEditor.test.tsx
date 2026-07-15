import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiRequestError, type EntityEnvelope } from "@/types/editing";
import { useEntityEditor } from "./useEntityEditor";

interface Draft {
  name: string;
  tags: string[];
}

const INITIAL: Draft = { name: "初始名称", tags: ["alpha"] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderEditor(
  submit: (draft: Draft, revision: string | undefined) => Promise<EntityEnvelope<Draft>>,
  onDirtyChange?: (dirty: boolean) => void
) {
  return renderHook(() =>
    useEntityEditor<Draft>({
      entity: INITIAL,
      revision: "rev-1",
      submit,
      onDirtyChange,
    })
  );
}

describe("useEntityEditor", () => {
  it("tracks dirty fields and resets the saved draft baseline", async () => {
    const submit = vi.fn().mockResolvedValue({
      entity: { name: "保存名称", tags: ["beta"] },
      revision: "rev-2",
    });
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "保存名称");
      hook.result.current.setField("tags", ["beta"]);
    });

    expect([...hook.result.current.dirtyFields]).toEqual(["name", "tags"]);
    expect(hook.result.current.isDirty).toBe(true);

    await act(async () => {
      await expect(hook.result.current.save()).resolves.toBe(true);
    });

    expect(submit).toHaveBeenCalledWith(
      { name: "保存名称", tags: ["beta"] },
      "rev-1"
    );
    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "保存名称", tags: ["beta"] },
      revision: "rev-2",
      isDirty: false,
      isSubmitting: false,
    });
    expect([...hook.result.current.dirtyFields]).toEqual([]);
  });

  it("cancels local edits and restores the baseline", () => {
    const hook = renderEditor(vi.fn());

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "临时名称");
      hook.result.current.cancel();
    });

    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: INITIAL,
      isDirty: false,
      fieldErrors: {},
      formError: null,
      conflict: null,
    });
  });

  it("keeps the editor open and maps a submit failure", async () => {
    const submit = vi.fn().mockRejectedValue(
      new ApiRequestError("校验失败", "validation_failed", { name: "名称不能为空" })
    );
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "");
    });
    await act(async () => {
      await expect(hook.result.current.save()).resolves.toBe(false);
    });

    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "", tags: ["alpha"] },
      isDirty: true,
      fieldErrors: { name: "名称不能为空" },
      formError: "校验失败",
      conflict: null,
    });
  });

  it("retains local edits and exposes a conflict envelope", async () => {
    const remote = { entity: { name: "远端名称", tags: ["remote"] }, revision: "rev-2" };
    const submit = vi.fn().mockRejectedValue(
      new ApiRequestError("记录已更新", "conflict", {}, {
        current_entity: remote.entity,
        current_revision: remote.revision,
      })
    );
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });
    await act(async () => {
      await expect(hook.result.current.save()).resolves.toBe(false);
    });

    expect(hook.result.current.draft).toEqual({ name: "本地名称", tags: ["alpha"] });
    expect([...hook.result.current.dirtyFields]).toEqual(["name"]);
    expect(hook.result.current.conflict).toEqual(remote);
  });

  it("recognizes the backend edit_conflict response code", async () => {
    const remote = { entity: { name: "远端名称", tags: ["remote"] }, revision: "rev-2" };
    const submit = vi.fn().mockRejectedValue(
      new ApiRequestError("记录已更新", "edit_conflict", {}, {
        current_entity: remote.entity,
        current_revision: remote.revision,
      })
    );
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });
    await act(async () => { await hook.result.current.save(); });

    expect(hook.result.current.conflict).toEqual(remote);
  });

  it("loads the remote conflict entity as the new baseline", async () => {
    const remote = { entity: { name: "远端名称", tags: ["remote"] }, revision: "rev-2" };
    const submit = vi.fn().mockRejectedValue(
      new ApiRequestError("记录已更新", "conflict", {}, {
        current_entity: remote.entity,
        current_revision: remote.revision,
      })
    );
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });
    await act(async () => { await hook.result.current.save(); });
    act(() => hook.result.current.loadRemote());

    expect(hook.result.current).toMatchObject({
      draft: remote.entity,
      revision: "rev-2",
      isDirty: false,
      conflict: null,
    });
    expect([...hook.result.current.dirtyFields]).toEqual([]);
  });

  it("reapplies only fields that were dirty before a conflict", async () => {
    const remote = { entity: { name: "远端名称", tags: ["remote"] }, revision: "rev-2" };
    const submit = vi.fn().mockRejectedValue(
      new ApiRequestError("记录已更新", "conflict", {}, {
        current_entity: remote.entity,
        current_revision: remote.revision,
      })
    );
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });
    await act(async () => { await hook.result.current.save(); });
    act(() => hook.result.current.reapplyLocal());

    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "本地名称", tags: ["remote"] },
      revision: "rev-2",
      conflict: null,
      isDirty: true,
    });
    expect([...hook.result.current.dirtyFields]).toEqual(["name"]);
  });

  it("prevents a second submit while the first is pending", async () => {
    const pending = deferred<EntityEnvelope<Draft>>();
    const submit = vi.fn(() => pending.promise);
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "待保存");
    });
    let first!: Promise<boolean>;
    let second!: Promise<boolean>;
    act(() => {
      first = hook.result.current.save();
      second = hook.result.current.save();
    });

    expect(submit).toHaveBeenCalledTimes(1);
    await expect(second).resolves.toBe(false);
    pending.resolve({ entity: { name: "待保存", tags: ["alpha"] }, revision: "rev-2" });
    await act(async () => { await expect(first).resolves.toBe(true); });
  });

  it("notifies dirty changes after edits, cancel, save, and unmount", async () => {
    const onDirtyChange = vi.fn();
    const submit = vi.fn().mockResolvedValue({ entity: INITIAL, revision: "rev-2" });
    const hook = renderEditor(submit, onDirtyChange);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "临时名称");
    });
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    act(() => hook.result.current.cancel());
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "保存名称");
    });
    await act(async () => { await hook.result.current.save(); });
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "卸载前编辑");
    });
    hook.unmount();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });
});
