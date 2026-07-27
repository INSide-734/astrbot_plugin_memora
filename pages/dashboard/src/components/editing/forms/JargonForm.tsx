import * as React from "react";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";
import type { JargonDraft, JargonMeaning } from "@/types";

type JargonValue = JargonDraft & Partial<Pick<JargonMeaning, "is_complete" | "count" | "last_inference_count" | "created_at" | "updated_at">> & { context_examples?: string[] };

export interface JargonFormProps {
  value: JargonValue;
  onChange(value: JargonValue): void;
  fieldErrors: FieldErrors;
  formErrors?: readonly string[];
  disabled?: boolean;
  mode: "create" | "edit";
}

const labelFallbacks: Record<string, string> = {
  "jargon.term": "Term", "jargon.groupId": "Group ID", "jargon.meaning": "Meaning", "table.confidence": "Confidence",
  "jargon.isJargon": "Is jargon", "jargon.isConfirmed": "Is confirmed", "jargon.isGlobal": "Is global",
  "jargon.isJargonDescription": "Marks whether this term is jargon", "jargon.isConfirmedDescription": "Marks whether this term was confirmed by an administrator", "jargon.isGlobalDescription": "Makes this meaning available across groups",
};

export function JargonForm({ value, onChange, fieldErrors, formErrors = [], disabled = false, mode }: JargonFormProps) {
  const { t } = useI18n();
  const label = (key: string) => { const translated = t(key); return translated === key ? labelFallbacks[key] ?? key : translated; };
  const [rangeError, setRangeError] = React.useState<string>();
  const [meaningError, setMeaningError] = React.useState<string>();
  const update = <K extends keyof JargonDraft>(field: K, next: JargonDraft[K]) => onChange({ ...value, [field]: next });
  const identityDisabled = disabled || mode === "edit";
  const validationErrors = { ...fieldErrors, ...(meaningError ? { meaning: meaningError } : {}), ...(rangeError ? { confidence: rangeError } : {}) };
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={validationErrors} formErrors={formErrors} focusInvalid={Object.keys(validationErrors).length > 0 || formErrors.length > 0}>
    {({ getFieldError, registerField }) => {
      const termError = getFieldError("term");
      const groupIdError = getFieldError("group_id");
      const meaningFieldError = getFieldError("meaning");
      const confidenceError = getFieldError("confidence");
      return <FieldGroup>
      <Field data-invalid={Boolean(termError)} data-disabled={identityDisabled}><FieldLabel htmlFor="jargon-term">{label("jargon.term")}</FieldLabel><Input ref={(element) => registerField("term", element)} id="jargon-term" value={value.term} aria-invalid={Boolean(termError)} aria-describedby={termError?.id} disabled={identityDisabled} onChange={(e) => update("term", e.currentTarget.value)} />{termError ? <FieldError id={termError.id}>{termError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(groupIdError)} data-disabled={identityDisabled}><FieldLabel htmlFor="jargon-group-id">{label("jargon.groupId")}</FieldLabel><Input ref={(element) => registerField("group_id", element)} id="jargon-group-id" value={value.group_id} aria-invalid={Boolean(groupIdError)} aria-describedby={groupIdError?.id} disabled={identityDisabled} onChange={(e) => update("group_id", e.currentTarget.value)} />{groupIdError ? <FieldError id={groupIdError.id}>{groupIdError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(meaningFieldError)}><FieldLabel htmlFor="jargon-meaning">{label("jargon.meaning")}</FieldLabel><Textarea ref={(element) => registerField("meaning", element)} id="jargon-meaning" value={value.meaning} aria-invalid={Boolean(meaningFieldError)} aria-describedby={meaningFieldError?.id} onChange={(e) => { const next = e.currentTarget.value; if (next.trim()) { setMeaningError(undefined); update("meaning", next); } else setMeaningError(label("jargon.meaningRequired")); }} />{meaningFieldError ? <FieldError id={meaningFieldError.id}>{meaningFieldError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(confidenceError)}><FieldLabel htmlFor="jargon-confidence">{label("table.confidence")}</FieldLabel><Input ref={(element) => registerField("confidence", element)} id="jargon-confidence" type="number" min="0" max="1" step="0.01" value={value.confidence} aria-invalid={Boolean(confidenceError)} aria-describedby={confidenceError?.id} onChange={(e) => { const next = Number(e.currentTarget.value); if (Number.isFinite(next) && next >= 0 && next <= 1) { setRangeError(undefined); update("confidence", next); } else setRangeError(label("jargon.confidenceRange")); }} />{confidenceError ? <FieldError id={confidenceError.id}>{confidenceError.message}</FieldError> : null}</Field>
      {(["is_jargon", "is_confirmed", "is_global"] as const).map((field) => { const fieldError = getFieldError(field); return <Field key={field} orientation="horizontal" data-invalid={Boolean(fieldError)} data-disabled={disabled}><Switch ref={(element) => registerField(field, element)} aria-label={label(`jargon.${field === "is_jargon" ? "isJargon" : field === "is_confirmed" ? "isConfirmed" : "isGlobal"}`)} aria-invalid={Boolean(fieldError)} aria-describedby={fieldError?.id} checked={value[field]} disabled={disabled} onCheckedChange={(checked) => update(field, checked)} /><div><FieldLabel>{label(`jargon.${field === "is_jargon" ? "isJargon" : field === "is_confirmed" ? "isConfirmed" : "isGlobal"}`)}</FieldLabel><FieldDescription>{label(`jargon.${field === "is_jargon" ? "isJargonDescription" : field === "is_confirmed" ? "isConfirmedDescription" : "isGlobalDescription"}`)}</FieldDescription>{fieldError ? <FieldError id={fieldError.id}>{fieldError.message}</FieldError> : null}</div></Field>; })}
      {value.context_examples?.length ? <div aria-label={label("jargon.contextExamples")}><FieldLabel>{label("jargon.contextExamples")}</FieldLabel><ul>{value.context_examples.map((example) => <li key={example}>{example}</li>)}</ul></div> : null}
      <div aria-label={label("jargon.storedMetadata")}>{value.is_complete !== undefined ? `${label("jargon.complete")}: ${value.is_complete}` : null}{value.count !== undefined ? ` ${label("jargon.count")}: ${value.count}` : null}</div>
    </FieldGroup>}}
  </EditFormLayout>;
}
