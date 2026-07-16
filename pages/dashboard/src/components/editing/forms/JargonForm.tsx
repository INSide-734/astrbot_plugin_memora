import * as React from "react";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
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
  disabled?: boolean;
  mode: "create" | "edit";
}

const labelFallbacks: Record<string, string> = {
  "jargon.term": "Term", "jargon.groupId": "Group ID", "jargon.meaning": "Meaning", "table.confidence": "Confidence",
  "jargon.isJargon": "Is jargon", "jargon.isConfirmed": "Is confirmed", "jargon.isGlobal": "Is global",
  "jargon.isJargonDescription": "Marks whether this term is jargon", "jargon.isConfirmedDescription": "Marks whether this term was confirmed by an administrator", "jargon.isGlobalDescription": "Makes this meaning available across groups",
};

export function JargonForm({ value, onChange, fieldErrors, disabled = false, mode }: JargonFormProps) {
  const { t } = useI18n();
  const label = (key: string) => { const translated = t(key); return translated === key ? labelFallbacks[key] ?? key : translated; };
  const [rangeError, setRangeError] = React.useState<string>();
  const [meaningError, setMeaningError] = React.useState<string>();
  const update = <K extends keyof JargonDraft>(field: K, next: JargonDraft[K]) => onChange({ ...value, [field]: next });
  const identityDisabled = disabled || mode === "edit";
  const confidenceError = fieldErrors.confidence ?? rangeError;
  const meaningFieldError = fieldErrors.meaning ?? meaningError;
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={{ ...fieldErrors, ...(meaningError ? { meaning: meaningError } : {}), ...(rangeError ? { confidence: rangeError } : {}) }} focusInvalid={Object.keys(fieldErrors).length > 0 || Boolean(meaningError) || Boolean(rangeError)}>
    {({ getFieldError }) => <FieldGroup>
      <Field data-disabled={identityDisabled}><FieldLabel htmlFor="jargon-term">{label("jargon.term")}</FieldLabel><Input id="jargon-term" value={value.term} disabled={identityDisabled} onChange={(e) => update("term", e.currentTarget.value)} /></Field>
      <Field data-disabled={identityDisabled}><FieldLabel htmlFor="jargon-group-id">{label("jargon.groupId")}</FieldLabel><Input id="jargon-group-id" value={value.group_id} disabled={identityDisabled} onChange={(e) => update("group_id", e.currentTarget.value)} /></Field>
      <Field data-invalid={Boolean(meaningFieldError)}><FieldLabel htmlFor="jargon-meaning">{label("jargon.meaning")}</FieldLabel><Textarea id="jargon-meaning" value={value.meaning} aria-invalid={Boolean(meaningFieldError)} aria-describedby={meaningFieldError ? "jargon-meaning-error" : undefined} onChange={(e) => { const next = e.currentTarget.value; if (next.trim()) { setMeaningError(undefined); update("meaning", next); } else setMeaningError(label("jargon.meaningRequired")); }} />{meaningFieldError ? <FieldError id="jargon-meaning-error">{meaningFieldError}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(confidenceError)}><FieldLabel htmlFor="jargon-confidence">{label("table.confidence")}</FieldLabel><Input id="jargon-confidence" type="number" min="0" max="1" step="0.01" value={value.confidence} aria-invalid={Boolean(confidenceError)} aria-describedby={confidenceError ? "jargon-confidence-error" : undefined} onChange={(e) => { const next = Number(e.currentTarget.value); if (Number.isFinite(next) && next >= 0 && next <= 1) { setRangeError(undefined); update("confidence", next); } else setRangeError(label("jargon.confidenceRange")); }} />{confidenceError ? <FieldError id={fieldErrors.confidence ? getFieldError("confidence")?.id : "jargon-confidence-error"}>{confidenceError}</FieldError> : null}</Field>
      {(["is_jargon", "is_confirmed", "is_global"] as const).map((field) => <Field key={field} orientation="horizontal" data-disabled={disabled}><Switch aria-label={label(`jargon.${field === "is_jargon" ? "isJargon" : field === "is_confirmed" ? "isConfirmed" : "isGlobal"}`)} checked={value[field]} disabled={disabled} onCheckedChange={(checked) => update(field, checked)} /><div><FieldLabel>{label(`jargon.${field === "is_jargon" ? "isJargon" : field === "is_confirmed" ? "isConfirmed" : "isGlobal"}`)}</FieldLabel><FieldDescription>{label(`jargon.${field === "is_jargon" ? "isJargonDescription" : field === "is_confirmed" ? "isConfirmedDescription" : "isGlobalDescription"}`)}</FieldDescription></div></Field>)}
      {value.context_examples?.length ? <div aria-label={label("jargon.contextExamples")}><FieldLabel>{label("jargon.contextExamples")}</FieldLabel><ul>{value.context_examples.map((example) => <li key={example}>{example}</li>)}</ul></div> : null}
      <div aria-label={label("jargon.storedMetadata")}>{value.is_complete !== undefined ? `${label("jargon.complete")}: ${value.is_complete}` : null}{value.count !== undefined ? ` ${label("jargon.count")}: ${value.count}` : null}</div>
    </FieldGroup>}
  </EditFormLayout>;
}
