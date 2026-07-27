import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { TagEditor } from "@/components/editing/TagEditor";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";

export interface KnowledgeDraft {
  title: string;
  content: string;
  category: string;
  confidence: number;
  tags: string[];
}

export interface DomainFormProps<T> {
  value: T;
  onChange(value: T): void;
  fieldErrors: FieldErrors;
  formErrors?: readonly string[];
  disabled?: boolean;
  mode: "create" | "edit";
}

export function KnowledgeForm({ value, onChange, fieldErrors, formErrors = [], disabled = false }: DomainFormProps<KnowledgeDraft>) {
  const { t } = useI18n();
  const update = <K extends keyof KnowledgeDraft>(field: K, next: KnowledgeDraft[K]) => onChange({ ...value, [field]: next });

  return (
    <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={fieldErrors} formErrors={formErrors} focusInvalid={Object.keys(fieldErrors).length > 0 || formErrors.length > 0}>
      {({ getFieldError, registerField }) => {
        const titleError = getFieldError("title");
        const contentError = getFieldError("content");
        const categoryError = getFieldError("category");
        const confidenceError = getFieldError("confidence");
        const tagsError = getFieldError("tags");
        return <>
          <Field data-invalid={Boolean(titleError)} data-disabled={disabled}><FieldLabel htmlFor="knowledge-title">{t("field.title")}</FieldLabel><Input ref={(element) => registerField("title", element)} id="knowledge-title" aria-invalid={Boolean(titleError)} aria-describedby={titleError?.id} disabled={disabled} value={value.title} onChange={(event) => update("title", event.currentTarget.value)} />{titleError ? <FieldError id={titleError.id}>{titleError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(contentError)} data-disabled={disabled}><FieldLabel htmlFor="knowledge-content">{t("field.content")}</FieldLabel><Textarea ref={(element) => registerField("content", element)} id="knowledge-content" aria-invalid={Boolean(contentError)} aria-describedby={contentError?.id} disabled={disabled} rows={5} value={value.content} onChange={(event) => update("content", event.currentTarget.value)} />{contentError ? <FieldError id={contentError.id}>{contentError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(categoryError)} data-disabled={disabled}><FieldLabel htmlFor="knowledge-category">{t("table.category")}</FieldLabel><Select value={value.category} onValueChange={(next) => next && update("category", next)} disabled={disabled}><SelectTrigger ref={(element) => registerField("category", element)} id="knowledge-category" aria-label={t("table.category")} aria-invalid={Boolean(categoryError)} aria-describedby={categoryError?.id}><span>{t(`category.${value.category}`)}</span></SelectTrigger><SelectContent><SelectGroup>{["fact", "concept", "rule", "event", "procedure"].map((category) => <SelectItem key={category} value={category}>{t(`category.${category}`)}</SelectItem>)}</SelectGroup></SelectContent></Select>{categoryError ? <FieldError id={categoryError.id}>{categoryError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(confidenceError)} data-disabled={disabled}><FieldLabel htmlFor="knowledge-confidence">{t("table.confidence")}</FieldLabel><Input ref={(element) => registerField("confidence", element)} id="knowledge-confidence" type="number" min="0" max="1" step="0.01" aria-invalid={Boolean(confidenceError)} aria-describedby={confidenceError?.id} disabled={disabled} value={value.confidence} onChange={(event) => update("confidence", Number(event.currentTarget.value))} />{confidenceError ? <FieldError id={confidenceError.id}>{confidenceError.message}</FieldError> : null}</Field>
          <Field data-invalid={Boolean(tagsError)} data-disabled={disabled}><FieldLabel>{t("field.tags")}</FieldLabel><TagEditor inputRef={(element) => registerField("tags", element)} label={t("field.tags")} getRemoveLabel={(tag) => t("tags.remove", tag)} values={value.tags} onChange={(next) => update("tags", next)} disabled={disabled} ariaInvalid={Boolean(tagsError)} ariaDescribedBy={tagsError?.id} />{tagsError ? <FieldError id={tagsError.id}>{tagsError.message}</FieldError> : null}</Field>
        </>;
      }}
    </EditFormLayout>
  );
}
