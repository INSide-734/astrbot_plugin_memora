export type FieldErrors = Record<string, string>;

export interface EntityEnvelope<T> {
  entity: T;
  revision: string;
}

export interface BatchFailure<I> {
  id: I;
  code: string;
  message: string;
}

export interface BatchResult<I> {
  total: number;
  succeeded_count: number;
  failed_count: number;
  succeeded_ids: I[];
  failures: BatchFailure<I>[];
}

export const BULK_CONFIRMATION_THRESHOLD = 20;

export class ApiRequestError extends Error {
  readonly code: string;
  readonly fieldErrors: FieldErrors;
  readonly data: Record<string, unknown>;

  constructor(
    message: string,
    code = "request_failed",
    fieldErrors: FieldErrors = {},
    data: Record<string, unknown> = {}
  ) {
    super(message);
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.data = data;
  }
}

export function editingErrorDetails(
  error: unknown,
  allowedFields: readonly string[],
  normalizeField: (name: string) => string | null = (name) => name,
): { fieldErrors: FieldErrors; formError: string | null } {
  if (!(error instanceof ApiRequestError)) {
    return {
      fieldErrors: {},
      formError: error instanceof Error ? error.message : String(error),
    };
  }

  const fieldErrors: FieldErrors = {};
  const formErrors: string[] = [];
  for (const [rawName, message] of Object.entries(error.fieldErrors)) {
    const unprefixedName = rawName.startsWith("changes.") ? rawName.slice("changes.".length) : rawName;
    const name = normalizeField(unprefixedName);
    if (name !== null && allowedFields.includes(name)) {
      fieldErrors[name] = message;
    } else if (!formErrors.includes(message)) {
      formErrors.push(message);
    }
  }
  return {
    fieldErrors,
    formError: formErrors.length > 0
      ? formErrors.join("; ")
      : Object.keys(error.fieldErrors).length === 0
        ? error.message
        : null,
  };
}
