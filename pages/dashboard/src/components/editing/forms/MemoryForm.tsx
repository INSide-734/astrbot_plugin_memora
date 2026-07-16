import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";

export interface MemoryDraft {
  content: string;
  importance: number;
  type: string;
  status: string;
}

export interface DomainFormProps<T> {
  value: T;
  onChange(value: T): void;
  fieldErrors: FieldErrors;
  formErrors?: readonly string[];
  disabled?: boolean;
  mode: "create" | "edit";
}

export function MemoryForm({ value, onChange, fieldErrors, formErrors = [], disabled = false }: DomainFormProps<MemoryDraft>) {
  const { t } = useI18n();
  const update = <K extends keyof MemoryDraft>(field: K, next: MemoryDraft[K]) => onChange({ ...value, [field]: next });

  return (
    <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={fieldErrors} formErrors={formErrors} focusInvalid={Object.keys(fieldErrors).length > 0 || formErrors.length > 0}>
      {({ getFieldError }) => {
        const contentError = getFieldError("content");
        const importanceError = getFieldError("importance");
        const typeError = getFieldError("type");
        const statusError = getFieldError("status");
        return <>
          <Field data-invalid={Boolean(contentError)} data-disabled={disabled}>
            <FieldLabel htmlFor="memory-content">{t("field.content")}</FieldLabel>
            <Textarea id="memory-content" aria-invalid={Boolean(contentError)} aria-describedby={contentError?.id} disabled={disabled} rows={5} value={value.content} onChange={(event) => update("content", event.currentTarget.value)} />
            {contentError ? <FieldError id={contentError.id}>{contentError.message}</FieldError> : null}
          </Field>
          <Field data-invalid={Boolean(importanceError)} data-disabled={disabled}>
            <FieldLabel htmlFor="memory-importance">{t("table.importance")}</FieldLabel>
            <Input id="memory-importance" type="number" min="0" max="10" step="0.1" aria-invalid={Boolean(importanceError)} aria-describedby={importanceError?.id} disabled={disabled} value={value.importance} onChange={(event) => update("importance", Number(event.currentTarget.value))} />
            {importanceError ? <FieldError id={importanceError.id}>{importanceError.message}</FieldError> : null}
          </Field>
          <Field data-invalid={Boolean(typeError)} data-disabled={disabled}>
            <FieldLabel htmlFor="memory-type">{t("table.type")}</FieldLabel>
            <Input id="memory-type" aria-invalid={Boolean(typeError)} aria-describedby={typeError?.id} disabled={disabled} value={value.type} onChange={(event) => update("type", event.currentTarget.value)} />
            {typeError ? <FieldError id={typeError.id}>{typeError.message}</FieldError> : null}
          </Field>
          <Field data-invalid={Boolean(statusError)} data-disabled={disabled}>
            <FieldLabel htmlFor="memory-status">{t("table.status")}</FieldLabel>
            <Select value={value.status} onValueChange={(next) => next && update("status", next)} disabled={disabled}>
              <SelectTrigger id="memory-status" aria-label={t("table.status")} aria-invalid={Boolean(statusError)} aria-describedby={statusError?.id}><span>{t(`filter.status${value.status.slice(0, 1).toUpperCase()}${value.status.slice(1)}`)}</span></SelectTrigger>
              <SelectContent><SelectGroup>
                <SelectItem value="active">{t("filter.statusActive")}</SelectItem>
                <SelectItem value="archived">{t("filter.statusArchived")}</SelectItem>
                <SelectItem value="deleted">{t("filter.statusDeleted")}</SelectItem>
              </SelectGroup></SelectContent>
            </Select>
            {statusError ? <FieldError id={statusError.id}>{statusError.message}</FieldError> : null}
          </Field>
        </>;
      }}
    </EditFormLayout>
  );
}
