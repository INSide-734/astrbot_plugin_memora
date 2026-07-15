import { act, renderHook } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ApiRequestError, type EntityEnvelope } from "@/types/editing";
import {
  type UseEntityEditorOptions,
  useEntityEditor,
} from "./useEntityEditor";

interface Draft {
  name: string;
  tags: string[];
}

const INITIAL: Draft = { name: "初始名称", tags: ["alpha"] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
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

function renderEditorWithProps(initialProps: UseEntityEditorOptions<Draft>) {
  return renderHook(
    (options: UseEntityEditorOptions<Draft>) => useEntityEditor<Draft>(options),
    { initialProps }
  );
}

function StrictModeWrapper({ children }: { children: ReactNode }) {
  return <StrictMode>{children}</StrictMode>;
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
      mode: "view",
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

  it("settles successful and failed saves after StrictMode effect replay", async () => {
    const submit = vi.fn()
      .mockResolvedValueOnce({ entity: { name: "已保存", tags: ["alpha"] }, revision: "rev-2" })
      .mockRejectedValueOnce(new ApiRequestError("校验失败", "validation_failed"));
    const hook = renderHook(
      () => useEntityEditor<Draft>({
        entity: INITIAL,
        revision: "rev-1",
        submit,
      }),
      { wrapper: StrictModeWrapper }
    );

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "已保存");
    });
    await act(async () => { await expect(hook.result.current.save()).resolves.toBe(true); });
    expect(hook.result.current).toMatchObject({
      mode: "view",
      isSubmitting: false,
      revision: "rev-2",
    });

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "失败名称");
    });
    await act(async () => { await expect(hook.result.current.save()).resolves.toBe(false); });
    expect(hook.result.current).toMatchObject({
      mode: "edit",
      isSubmitting: false,
      formError: "校验失败",
    });
  });

  it("rebases a clean editor to changed entity props", () => {
    const submit = vi.fn();
    const hook = renderEditorWithProps({
      entity: INITIAL,
      revision: "rev-1",
      submit,
    });

    hook.rerender({
      entity: { name: "服务端名称", tags: ["remote"] },
      revision: "rev-2",
      submit,
    });

    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "服务端名称", tags: ["remote"] },
      revision: "rev-2",
      isDirty: false,
      fieldErrors: {},
      formError: null,
      conflict: null,
    });
  });

  it("discards a dirty draft when changed entity props rebase it", () => {
    const submit = vi.fn();
    const hook = renderEditorWithProps({
      entity: INITIAL,
      revision: "rev-1",
      submit,
    });
    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });

    hook.rerender({
      entity: { name: "服务端名称", tags: ["remote"] },
      revision: "rev-2",
      submit,
    });

    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "服务端名称", tags: ["remote"] },
      revision: "rev-2",
      isDirty: false,
    });
  });

  it("preserves equivalent props and uses the latest submit callback", async () => {
    const firstSubmit = vi.fn();
    const latestSubmit = vi.fn().mockResolvedValue({
      entity: { name: "已保存", tags: ["alpha"] },
      revision: "rev-2",
    });
    const hook = renderEditorWithProps({
      entity: INITIAL,
      revision: "rev-1",
      submit: firstSubmit,
    });
    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "本地名称");
    });
    const save = hook.result.current.save;

    hook.rerender({
      entity: { name: "初始名称", tags: ["alpha"] },
      revision: "rev-1",
      submit: latestSubmit,
    });
    expect(hook.result.current).toMatchObject({ mode: "edit", isDirty: true });

    await act(async () => { await expect(save()).resolves.toBe(true); });
    expect(firstSubmit).not.toHaveBeenCalled();
    expect(latestSubmit).toHaveBeenCalledWith(
      { name: "本地名称", tags: ["alpha"] },
      "rev-1"
    );
  });

  it("ignores an old save result after a prop rebase", async () => {
    const pending = deferred<EntityEnvelope<Draft>>();
    const submit = vi.fn(() => pending.promise);
    const hook = renderEditorWithProps({
      entity: INITIAL,
      revision: "rev-1",
      submit,
    });
    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "旧保存");
    });
    let save!: Promise<boolean>;
    act(() => { save = hook.result.current.save(); });

    hook.rerender({
      entity: { name: "新远端", tags: ["remote"] },
      revision: "rev-2",
      submit,
    });
    pending.resolve({ entity: { name: "旧保存", tags: ["alpha"] }, revision: "rev-old" });
    await act(async () => { await expect(save).resolves.toBe(false); });

    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "新远端", tags: ["remote"] },
      revision: "rev-2",
      isSubmitting: false,
      isDirty: false,
    });
  });

  it("does not repeat dirty notifications when only the callback identity changes", () => {
    const firstCallback = vi.fn();
    const latestCallback = vi.fn();
    const submit = vi.fn();
    const hook = renderEditorWithProps({
      entity: INITIAL,
      revision: "rev-1",
      submit,
      onDirtyChange: firstCallback,
    });
    firstCallback.mockClear();

    hook.rerender({
      entity: { name: "初始名称", tags: ["alpha"] },
      revision: "rev-1",
      submit,
      onDirtyChange: latestCallback,
    });
    expect(firstCallback).not.toHaveBeenCalled();
    expect(latestCallback).not.toHaveBeenCalled();

    act(() => hook.result.current.setField("name", "本地名称"));
    expect(latestCallback).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("releases submission state when cloning the draft throws synchronously", async () => {
    const submit = vi.fn().mockResolvedValue({
      entity: { name: "已保存", tags: ["alpha"] },
      revision: "rev-2",
    });
    const hook = renderEditor(submit);
    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "待保存");
    });
    hook.result.current.draft.tags = new Proxy(["alpha"], {
      get(target, property, receiver) {
        if (property === "map") throw new Error("克隆失败");
        return Reflect.get(target, property, receiver);
      },
    });

    await act(async () => { await expect(hook.result.current.save()).resolves.toBe(false); });
    expect(hook.result.current).toMatchObject({
      isSubmitting: false,
      formError: "克隆失败",
    });

    hook.result.current.draft.tags = ["alpha"];
    await act(async () => { await expect(hook.result.current.save()).resolves.toBe(true); });
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("invalidates a pending save on cancel so a newer save controls the editor", async () => {
    const oldRequest = deferred<EntityEnvelope<Draft>>();
    const newRequest = deferred<EntityEnvelope<Draft>>();
    const submit = vi.fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => newRequest.promise);
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "旧草稿");
    });
    let oldSave!: Promise<boolean>;
    act(() => { oldSave = hook.result.current.save(); });

    act(() => hook.result.current.cancel());
    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: INITIAL,
      isDirty: false,
      isSubmitting: false,
    });

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "新草稿");
    });
    let newSave!: Promise<boolean>;
    act(() => { newSave = hook.result.current.save(); });
    expect(submit).toHaveBeenCalledTimes(2);
    expect(hook.result.current.isSubmitting).toBe(true);

    oldRequest.resolve({ entity: { name: "旧保存", tags: ["alpha"] }, revision: "rev-old" });
    await act(async () => { await expect(oldSave).resolves.toBe(false); });
    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "新草稿", tags: ["alpha"] },
      revision: "rev-1",
      isDirty: true,
      isSubmitting: true,
    });

    newRequest.resolve({ entity: { name: "新草稿", tags: ["alpha"] }, revision: "rev-2" });
    await act(async () => { await expect(newSave).resolves.toBe(true); });
    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "新草稿", tags: ["alpha"] },
      revision: "rev-2",
      isSubmitting: false,
    });
  });

  it("invalidates a pending save when a local edit supersedes its draft", async () => {
    const request = deferred<EntityEnvelope<Draft>>();
    const submit = vi.fn(() => request.promise);
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "已提交草稿");
    });
    let save!: Promise<boolean>;
    act(() => { save = hook.result.current.save(); });

    act(() => hook.result.current.setField("name", "较新本地编辑"));
    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "较新本地编辑", tags: ["alpha"] },
      isDirty: true,
      isSubmitting: false,
    });

    request.resolve({ entity: { name: "已提交草稿", tags: ["alpha"] }, revision: "rev-2" });
    await act(async () => { await expect(save).resolves.toBe(false); });
    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "较新本地编辑", tags: ["alpha"] },
      revision: "rev-1",
      isDirty: true,
      isSubmitting: false,
    });
  });

  it("ignores a superseded save rejection while a newer save is pending", async () => {
    const oldRequest = deferred<EntityEnvelope<Draft>>();
    const newRequest = deferred<EntityEnvelope<Draft>>();
    const submit = vi.fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => newRequest.promise);
    const hook = renderEditor(submit);

    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "旧草稿");
    });
    let oldSave!: Promise<boolean>;
    act(() => { oldSave = hook.result.current.save(); });

    act(() => hook.result.current.cancel());
    act(() => {
      hook.result.current.beginEdit();
      hook.result.current.setField("name", "新草稿");
    });
    let newSave!: Promise<boolean>;
    act(() => { newSave = hook.result.current.save(); });
    expect(hook.result.current.isSubmitting).toBe(true);

    oldRequest.reject(new Error("旧请求失败"));
    await act(async () => { await expect(oldSave).resolves.toBe(false); });
    expect(hook.result.current).toMatchObject({
      mode: "edit",
      draft: { name: "新草稿", tags: ["alpha"] },
      revision: "rev-1",
      isDirty: true,
      isSubmitting: true,
    });

    newRequest.resolve({ entity: { name: "新草稿", tags: ["alpha"] }, revision: "rev-2" });
    await act(async () => { await expect(newSave).resolves.toBe(true); });
    expect(hook.result.current).toMatchObject({
      mode: "view",
      draft: { name: "新草稿", tags: ["alpha"] },
      revision: "rev-2",
      isDirty: false,
      isSubmitting: false,
    });
  });
});
