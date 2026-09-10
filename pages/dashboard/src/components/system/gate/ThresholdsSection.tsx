import {
  Field,
  FieldContent,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/hooks/useI18n";
import type { GateProfileData, GateThresholds } from "@/types/config";
import { validateThresholdCross } from "./validation";

export interface ThresholdsSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

interface RangeRowProps {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onChange: (value: number) => void;
}

/** 原生 range + 数字输入联动行（RecallPage 先例，无 Slider 组件）。 */
function RangeRow({
  id,
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
}: RangeRowProps) {
  return (
    <Field>
      <Label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      <div className="flex min-w-0 items-center gap-3">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
          className="h-6 w-full max-w-56 accent-primary md:h-1.5"
        />
        <Input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => {
            const next = Number(event.currentTarget.valueAsNumber);
            if (Number.isFinite(next)) onChange(next);
          }}
          className="h-8 w-20 shrink-0"
        />
      </div>
    </Field>
  );
}

/** 阈值（交叉校验）与算法参数。 */
export function ThresholdsSection({
  profile,
  disabled,
  onChange,
}: ThresholdsSectionProps) {
  const { t } = useI18n();
  const thresholds = profile.thresholds;
  const crossInvalid = validateThresholdCross(thresholds);

  const changeThreshold = (patch: Partial<GateThresholds>) => {
    onChange({ thresholds: { ...thresholds, ...patch } });
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.thresholds.title")}</FieldLegend>
      <p className="text-sm text-muted-foreground">{t("gate.help.thresholds")}</p>
      <FieldGroup>
        <RangeRow
          id="gate-threshold-min-deterministic"
          label={t("gate.thresholds.minDeterministic")}
          value={thresholds.min_deterministic_score}
          min={0}
          max={1}
          step={0.01}
          disabled={disabled}
          onChange={(value) =>
            changeThreshold({ min_deterministic_score: value })
          }
        />
        <RangeRow
          id="gate-threshold-min-judge"
          label={t("gate.thresholds.minJudge")}
          value={thresholds.min_judge_score}
          min={0}
          max={1}
          step={0.01}
          disabled={disabled}
          onChange={(value) => changeThreshold({ min_judge_score: value })}
        />
        <RangeRow
          id="gate-threshold-min-inference"
          label={t("gate.thresholds.minInference")}
          value={thresholds.min_inference_score}
          min={0}
          max={1}
          step={0.01}
          disabled={disabled}
          onChange={(value) => changeThreshold({ min_inference_score: value })}
        />
        {crossInvalid ? (
          <FieldError>{t("gate.thresholds.crossError")}</FieldError>
        ) : null}
      </FieldGroup>

      <FieldSet className="mt-1">
        <FieldLegend variant="label">{t("gate.scoring.group")}</FieldLegend>
        <FieldGroup>
          <RangeRow
            id="gate-scoring-token-weight"
            label={t("gate.scoring.tokenWeight")}
            value={profile.scoring.token_weight}
            min={0}
            max={2}
            step={0.1}
            disabled={disabled}
            onChange={(value) =>
              onChange({ scoring: { ...profile.scoring, token_weight: value } })
            }
          />
          <Field orientation="horizontal">
            <FieldContent>
              <FieldTitle>{t("gate.scoring.sequenceEnabled")}</FieldTitle>
            </FieldContent>
            <Switch
              checked={profile.scoring.sequence_enabled}
              disabled={disabled}
              onCheckedChange={(checked) =>
                onChange({
                  scoring: { ...profile.scoring, sequence_enabled: checked },
                })
              }
              aria-label={t("gate.scoring.sequenceEnabled")}
            />
          </Field>
          <RangeRow
            id="gate-scoring-sequence-weight"
            label={t("gate.scoring.sequenceWeight")}
            value={profile.scoring.sequence_weight}
            min={0}
            max={2}
            step={0.1}
            disabled={disabled}
            onChange={(value) =>
              onChange({
                scoring: { ...profile.scoring, sequence_weight: value },
              })
            }
          />
          <Field>
            <Label
              htmlFor="gate-scoring-max-references"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.scoring.maxReferences")}
            </Label>
            <Input
              id="gate-scoring-max-references"
              type="number"
              min={1}
              max={16}
              step={1}
              value={profile.references.max_references}
              disabled={disabled}
              onChange={(event) => {
                const next = Math.trunc(event.currentTarget.valueAsNumber);
                if (Number.isFinite(next) && next >= 1 && next <= 16) {
                  onChange({ references: { max_references: next } });
                }
              }}
              className="h-8 w-20"
            />
          </Field>
          <Field>
            <Label
              htmlFor="gate-scoring-min-summary-chars"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.scoring.minSummaryChars")}
            </Label>
            <Input
              id="gate-scoring-min-summary-chars"
              type="number"
              min={1}
              max={100}
              step={1}
              value={profile.quality.min_summary_chars}
              disabled={disabled}
              onChange={(event) => {
                const next = Math.trunc(event.currentTarget.valueAsNumber);
                if (Number.isFinite(next) && next >= 1 && next <= 100) {
                  onChange({ quality: { min_summary_chars: next } });
                }
              }}
              className="h-8 w-20"
            />
          </Field>
        </FieldGroup>
      </FieldSet>
    </FieldSet>
  );
}
