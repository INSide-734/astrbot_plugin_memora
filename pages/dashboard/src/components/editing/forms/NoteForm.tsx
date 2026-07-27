import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { TagEditor } from "@/components/editing/TagEditor";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
  formErrors?: readonly string[];
  disabled?: boolean;
  mode: "create" | "edit";
}

export function NoteForm({ value, onChange, fieldErrors, formErrors = [], disabled = false, mode }: DomainFormProps<NoteDraft>) {
  const { t } = useI18n();
  const update = <K extends keyof NoteDraft>(field: K, next: NoteDraft[K]) => onChange({ ...value, [field]: next });
  const statusItems = [
    { value: "active", label: t("status.active") },
    { value: "archived", label: t("status.archived") },
    { value: "deleted", label: t("status.deleted") },
  ];

  return (
    <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={fieldErrors} formErrors={formErrors} focusInvalid={Object.keys(fieldErrors).length > 0 || formErrors.length > 0}>
      {({ getFieldError, registerField }) => {
        const titleError = getFieldError("title");
        const contentError = getFieldError("content");
        const tagsError = getFieldError("tags");
        const statusError = getFieldError("status");
        return <>
          <Field data-invalid={Boolean(titleError)} data-disabled={disabled}><FieldLabel htmlFor="note-title">{t("field.title")}</FieldLabel><Input ref={(element) => registerField("title", element)} id="note-title" aria-invalid={Boolean(titleError)} aria-describedby={titleError?.id} disabled={disabled} value={value.title} onChange={(event) => update("title", event.currentTarget.value)} />{titleError ? <FieldError id={titleError.id}>{titleError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(contentError)} data-disabled={disabled}><FieldLabel htmlFor="note-content">{t("field.content")}</FieldLabel><Textarea ref={(element) => registerField("content", element)} id="note-content" aria-invalid={Boolean(contentError)} aria-describedby={contentError?.id} disabled={disabled} rows={6} value={value.content} onChange={(event) => update("content", event.currentTarget.value)} />{contentError ? <FieldError id={contentError.id}>{contentError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(tagsError)} data-disabled={disabled}><FieldLabel>{t("field.tags")}</FieldLabel><TagEditor inputRef={(element) => registerField("tags", element)} label={t("field.tags")} getRemoveLabel={(tag) => t("tags.remove", tag)} values={value.tags} onChange={(next) => update("tags", next)} disabled={disabled} ariaInvalid={Boolean(tagsError)} ariaDescribedBy={tagsError?.id} />{tagsError ? <FieldError id={tagsError.id}>{tagsError.message}</FieldError> : null}</Field>
          {mode === "edit" ? <Field data-invalid={Boolean(statusError)} data-disabled={disabled}><FieldLabel htmlFor="note-status">{t("table.status")}</FieldLabel><Select items={statusItems} value={value.status} onValueChange={(next) => next && update("status", next)} disabled={disabled}><SelectTrigger ref={(element) => registerField("status", element)} id="note-status" aria-label={t("table.status")} aria-invalid={Boolean(statusError)} aria-describedby={statusError?.id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{statusItems.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectGroup></SelectContent></Select>{statusError ? <FieldError id={statusError.id}>{statusError.message}</FieldError> : null}</Field> : null}
        </>;
      }}
    </EditFormLayout>
  );
}
