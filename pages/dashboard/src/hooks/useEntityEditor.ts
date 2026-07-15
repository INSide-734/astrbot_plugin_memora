import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  type EntityEnvelope,
  type FieldErrors,
} from "@/types/editing";

export interface UseEntityEditorOptions<T extends object> {
  entity: T;
  revision?: string;
  onDirtyChange?: (dirty: boolean) => void;
  submit: (
    draft: T,
    expectedRevision: string | undefined
  ) => Promise<EntityEnvelope<T>>;
}

export interface EntityEditorState<T extends object> {
  mode: "view" | "edit";
  draft: T;
  revision?: string;
  dirtyFields: ReadonlySet<keyof T>;
  isDirty: boolean;
  isSubmitting: boolean;
  fieldErrors: FieldErrors;
  formError: string | null;
  conflict: EntityEnvelope<T> | null;
  beginEdit(): void;
  setField<K extends keyof T>(field: K, value: T[K]): void;
  cancel(): void;
  save(): Promise<boolean>;
  loadRemote(): void;
  reapplyLocal(): void;
}

interface EditorData<T extends object> {
  baseline: T;
  draft: T;
  revision?: string;
  mode: "view" | "edit";
  dirtyFields: Set<keyof T>;
  isSubmitting: boolean;
  fieldErrors: FieldErrors;
  formError: string | null;
  conflict: EntityEnvelope<T> | null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function cloneValue<T>(value: T): T {
  if (Array.isArray(value)) return value.map(cloneValue) as T;
  if (isPlainObject(value)) {
    const clone: Record<string, unknown> = {};
    for (const key of Object.keys(value)) clone[key] = cloneValue(value[key]);
    return clone as T;
  }
  return value;
}

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, index) => valuesEqual(value, right[index]));
  }
  if (isPlainObject(left) && isPlainObject(right)) {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && valuesEqual(left[key], right[key]));
  }
  return false;
}

function initialData<T extends object>(entity: T, revision?: string): EditorData<T> {
  const baseline = cloneValue(entity);
  return {
    baseline,
    draft: cloneValue(baseline),
    revision,
    mode: "view",
    dirtyFields: new Set(),
    isSubmitting: false,
    fieldErrors: {},
    formError: null,
    conflict: null,
  };
}

function conflictEnvelope<T extends object>(error: ApiRequestError): EntityEnvelope<T> | null {
  const entity = error.data.current_entity;
  const revision = error.data.current_revision;
  return isPlainObject(entity) && typeof revision === "string"
    ? { entity: cloneValue(entity as T), revision }
    : null;
}

export function useEntityEditor<T extends object>(
  options: UseEntityEditorOptions<T>
): EntityEditorState<T> {
  const [state, setState] = useState(() => initialData(options.entity, options.revision));
  const stateRef = useRef(state);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const dirtyCallbackRef = useRef(options.onDirtyChange);
  stateRef.current = state;
  dirtyCallbackRef.current = options.onDirtyChange;

  const isDirty = state.dirtyFields.size > 0;

  useEffect(() => {
    options.onDirtyChange?.(isDirty);
  }, [isDirty, options.onDirtyChange]);

  useEffect(() => () => {
    mountedRef.current = false;
    dirtyCallbackRef.current?.(false);
  }, []);

  const beginEdit = useCallback(() => {
    setState((previous) => ({
      ...previous,
      mode: "edit",
      fieldErrors: {},
      formError: null,
    }));
  }, []);

  const setField = useCallback(<K extends keyof T>(field: K, value: T[K]) => {
    setState((previous) => {
      const dirtyFields = new Set(previous.dirtyFields);
      if (valuesEqual(previous.baseline[field], value)) dirtyFields.delete(field);
      else dirtyFields.add(field);
      return {
        ...previous,
        draft: { ...previous.draft, [field]: cloneValue(value) },
        dirtyFields,
        fieldErrors: Object.fromEntries(
          Object.entries(previous.fieldErrors).filter(([name]) => name !== String(field))
        ),
        formError: null,
      };
    });
  }, []);

  const cancel = useCallback(() => {
    setState((previous) => ({
      ...previous,
      draft: cloneValue(previous.baseline),
      mode: "view",
      dirtyFields: new Set(),
      fieldErrors: {},
      formError: null,
      conflict: null,
    }));
  }, []);

  const save = useCallback(async (): Promise<boolean> => {
    const current = stateRef.current;
    if (submittingRef.current || current.dirtyFields.size === 0) return false;

    submittingRef.current = true;
    const submittedDraft = cloneValue(current.draft);
    setState((previous) => ({
      ...previous,
      isSubmitting: true,
      fieldErrors: {},
      formError: null,
    }));

    try {
      const saved = await options.submit(submittedDraft, current.revision);
      if (!mountedRef.current) return true;
      setState((previous) => ({
        ...previous,
        baseline: cloneValue(saved.entity),
        draft: cloneValue(saved.entity),
        revision: saved.revision,
        mode: "view",
        dirtyFields: new Set(),
        isSubmitting: false,
        fieldErrors: {},
        formError: null,
        conflict: null,
      }));
      return true;
    } catch (error) {
      if (!mountedRef.current) return false;
      const apiError = error instanceof ApiRequestError ? error : null;
      const remote = apiError && (apiError.code === "conflict" || apiError.code === "edit_conflict")
        ? conflictEnvelope<T>(apiError)
        : null;
      setState((previous) => ({
        ...previous,
        isSubmitting: false,
        fieldErrors: apiError?.fieldErrors ?? {},
        formError: error instanceof Error ? error.message : String(error),
        conflict: remote,
      }));
      return false;
    } finally {
      submittingRef.current = false;
    }
  }, [options]);

  const loadRemote = useCallback(() => {
    setState((previous) => {
      if (!previous.conflict) return previous;
      const baseline = cloneValue(previous.conflict.entity);
      return {
        ...previous,
        baseline,
        draft: cloneValue(baseline),
        revision: previous.conflict.revision,
        dirtyFields: new Set(),
        fieldErrors: {},
        formError: null,
        conflict: null,
      };
    });
  }, []);

  const reapplyLocal = useCallback(() => {
    setState((previous) => {
      if (!previous.conflict) return previous;
      const baseline = cloneValue(previous.conflict.entity);
      const draft = cloneValue(baseline);
      for (const field of previous.dirtyFields) {
        draft[field] = cloneValue(previous.draft[field]);
      }
      return {
        ...previous,
        baseline,
        draft,
        revision: previous.conflict.revision,
        mode: "edit",
        dirtyFields: new Set(previous.dirtyFields),
        fieldErrors: {},
        formError: null,
        conflict: null,
      };
    });
  }, []);

  return {
    mode: state.mode,
    draft: state.draft,
    revision: state.revision,
    dirtyFields: state.dirtyFields,
    isDirty,
    isSubmitting: state.isSubmitting,
    fieldErrors: state.fieldErrors,
    formError: state.formError,
    conflict: state.conflict,
    beginEdit,
    setField,
    cancel,
    save,
    loadRemote,
    reapplyLocal,
  };
}
