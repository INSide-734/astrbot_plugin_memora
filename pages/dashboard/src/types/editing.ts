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
    this.name = "ApiRequestError";
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.data = data;
  }
}
