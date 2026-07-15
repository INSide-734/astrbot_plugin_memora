import { EditFormLayout } from "@/components/editing/EditFormLayout";
import { TagEditor } from "@/components/editing/TagEditor";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";

export interface NoteDraft {
  title: string;
  content: string;
  tags: string[];
  status: string;
}

export interface DomainFormProps<T> {
  value: T;
  onChange(value: T): void;
  fieldErrors: FieldErrors;
  disabled?: boolean;
  mode: "create" | "edit";
}

export function NoteForm({ value, onChange, fieldErrors, disabled = false, mode }: DomainFormProps<NoteDraft>) {
  const { t } = useI18n();
  const update = <K extends keyof NoteDraft>(field: K, next: NoteDraft[K]) => onChange({ ...value, [field]: next });

  return (
    <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={fieldErrors} focusInvalid={Object.keys(fieldErrors).length > 0}>
      {({ getFieldError }) => {
        const titleError = getFieldError("title");
        const contentError = getFieldError("content");
        const tagsError = getFieldError("tags");
        const statusError = getFieldError("status");
        return <>
          <Field data-invalid={Boolean(titleError)} data-disabled={disabled}><FieldLabel htmlFor="note-title">{t("field.title")}</FieldLabel><Input id="note-title" aria-invalid={Boolean(titleError)} aria-describedby={titleError?.id} disabled={disabled} value={value.title} onChange={(event) => update("title", event.currentTarget.value)} />{titleError ? <FieldError id={titleError.id}>{titleError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(contentError)} data-disabled={disabled}><FieldLabel htmlFor="note-content">{t("field.content")}</FieldLabel><Textarea id="note-content" aria-invalid={Boolean(contentError)} aria-describedby={contentError?.id} disabled={disabled} rows={6} value={value.content} onChange={(event) => update("content", event.currentTarget.value)} />{contentError ? <FieldError id={contentError.id}>{contentError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(tagsError)} data-disabled={disabled}><FieldLabel>{t("field.tags")}</FieldLabel><TagEditor label={t("field.tags")} getRemoveLabel={(tag) => t("tags.remove", tag)} values={value.tags} onChange={(next) => update("tags", next)} disabled={disabled} />{tagsError ? <FieldError id={tagsError.id}>{tagsError.message}</FieldError> : null}</Field>
          {mode === "edit" ? <Field data-invalid={Boolean(statusError)} data-disabled={disabled}><FieldLabel htmlFor="note-status">{t("table.status")}</FieldLabel><Select value={value.status} onValueChange={(next) => next && update("status", next)} disabled={disabled}><SelectTrigger id="note-status" aria-label={t("table.status")} aria-invalid={Boolean(statusError)} aria-describedby={statusError?.id}><span>{t(`status.${value.status}`)}</span></SelectTrigger><SelectContent><SelectGroup><SelectItem value="active">{t("filter.statusActive")}</SelectItem><SelectItem value="archived">{t("filter.statusArchived")}</SelectItem><SelectItem value="deleted">{t("filter.statusDeleted")}</SelectItem></SelectGroup></SelectContent></Select>{statusError ? <FieldError id={statusError.id}>{statusError.message}</FieldError> : null}</Field> : null}
        </>;
      }}
    </EditFormLayout>
  );
}
