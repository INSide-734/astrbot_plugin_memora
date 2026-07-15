import { useEffect, useId, useRef } from "react";

import { FieldError, FieldGroup } from "@/components/ui/field";

export type FieldErrors = Record<string, string | undefined>;

export interface FieldErrorDetails {
  id: string;
  message: string;
}

export interface EditFormLayoutProps {
  summaryLabel: string;
  fieldErrors?: FieldErrors;
  formErrors?: readonly string[];
  focusInvalid?: boolean;
  children: (helpers: {
    registerField: (name: string, element: HTMLElement | null) => void;
    getFieldError: (name: string) => FieldErrorDetails | undefined;
  }) => React.ReactNode;
}

function errorId(prefix: string, name: string) {
  return `${prefix}-error-${name.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

export function EditFormLayout({
  summaryLabel,
  fieldErrors = {},
  formErrors = [],
  focusInvalid = false,
  children,
}: EditFormLayoutProps) {
  const prefix = useId().replace(/:/g, "");
  const fields = useRef(new Map<string, HTMLElement>());
  const alertRef = useRef<HTMLDivElement>(null);
  const errors = Object.entries(fieldErrors).filter((entry): entry is [string, string] => Boolean(entry[1]));

  useEffect(() => {
    if (!focusInvalid || errors.length === 0) return;
    const firstField = fields.current.get(errors[0][0]);
    if (firstField) firstField.focus();
    else alertRef.current?.focus();
  }, [errors, focusInvalid]);

  return (
    <FieldGroup>
      {errors.length > 0 || formErrors.length > 0 ? (
        <div ref={alertRef} role="alert" tabIndex={-1} className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          <p className="font-medium">{summaryLabel}</p>
          <ul className="mt-1 list-disc pl-5">
            {formErrors.map((error, index) => <li key={`${error}-${index}`}>{error}</li>)}
            {errors.map(([name, message]) => <li key={name}><a href={`#${errorId(prefix, name)}`}>{message}</a></li>)}
          </ul>
        </div>
      ) : null}
      {children({
        registerField: (name, element) => {
          if (element) fields.current.set(name, element);
          else fields.current.delete(name);
        },
        getFieldError: (name) => {
          const message = fieldErrors[name];
          return message ? { id: errorId(prefix, name), message } : undefined;
        },
      })}
      {errors.map(([name, message]) => <FieldError key={name} id={errorId(prefix, name)}>{message}</FieldError>)}
    </FieldGroup>
  );
}
