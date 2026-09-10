import { Badge } from "@/components/ui/Badge";
import { DetailField, DetailGrid, DetailSection } from "@/components/editing/EntityDetail";
import { useI18n } from "@/hooks/useI18n";
import { formatDashboardPercent } from "@/lib/i18n";
import type { ProfileDraft } from "@/types";

export interface ProfileProvenance {
  origin?: string;
}

export interface ProfileTag {
  name?: string;
  category?: string;
  value?: string;
  confidence: number;
  source?: string;
  provenance?: ProfileProvenance;
}

export type ProfileTagValue = ProfileTag | string;

export type ProfilePreferencesDetail = Partial<ProfileDraft["preferences"]> & {
  provenance?: ProfileProvenance;
};

interface ProfileProvenanceDetailsProps {
  tags: ProfileTagValue[];
  preferences?: ProfilePreferencesDetail;
  locale: string;
}

/** 返回标签的显示文本，同时兼容旧字符串标签。 */
export function profileTagValue(tag: ProfileTagValue): string {
  return typeof tag === "string" ? tag : tag.value ?? tag.name ?? "";
}

/** 返回标签分类，同时兼容缺少分类的旧数据。 */
export function profileTagCategory(tag: ProfileTagValue): string {
  return typeof tag === "string" ? "interest" : tag.category ?? "interest";
}

/** 返回有限数值置信度，非法值由展示层显示为缺失。 */
export function profileTagConfidence(tag: ProfileTagValue): number {
  return typeof tag === "string" ? 0.5 : Number(tag.confidence ?? 0.5);
}

/** 从 provenance 或旧 source 字段解析 manual/derived 来源。 */
function profileOrigin(value: { provenance?: ProfileProvenance; source?: string } | undefined): "manual" | "derived" | null {
  const origin = value?.provenance?.origin?.trim().toLowerCase();
  if (origin === "manual" || origin === "derived") return origin;
  if (value?.source === "manual") return "manual";
  if (value?.source) return "derived";
  return null;
}

/** 返回画像来源对应的三语言翻译键。 */
function profileSourceKey(origin: "manual" | "derived"): string {
  return origin === "manual" ? "profile.source.manual" : "profile.source.derived";
}

/** 展示画像标签、偏好及其人工或派生来源。 */
export function ProfileProvenanceDetails({ tags, preferences, locale }: ProfileProvenanceDetailsProps) {
  const { t } = useI18n();
  const preferencesOrigin = profileOrigin(preferences);

  return (
    <>
      <DetailSection title={t("table.tags")}>
        <div className="space-y-2">
          {tags.length ? tags.map((tag, index) => {
            const category = profileTagCategory(tag).trim() || "--";
            const value = profileTagValue(tag).trim() || "--";
            const confidenceValue = profileTagConfidence(tag);
            const confidence = Number.isFinite(confidenceValue)
              ? formatDashboardPercent(confidenceValue, locale, { maximumFractionDigits: 0 })
              : "--";
            const origin = typeof tag === "string" ? null : profileOrigin(tag);
            return (
              <div key={`${category}-${value}-${index}`} className="grid min-w-0 gap-3 rounded-lg border border-border/70 bg-muted/20 p-3 sm:grid-cols-[minmax(7rem,0.8fr)_minmax(0,1.6fr)_minmax(7rem,0.7fr)_minmax(6rem,0.6fr)]">
                <DetailField label={t("profile.tagCategory")}>{category}</DetailField>
                <DetailField label={t("profile.tagValue")}>{value}</DetailField>
                <DetailField label={t("profile.tagConfidence")}>{confidence}</DetailField>
                <DetailField label={t("profile.sourceLabel")}>
                  {origin ? <Badge variant={origin === "manual" ? "outline" : "secondary"}>{t(profileSourceKey(origin))}</Badge> : "--"}
                </DetailField>
              </div>
            );
          }) : <span className="text-sm text-muted-foreground">--</span>}
        </div>
      </DetailSection>
      <DetailSection title={t("profile.preferences")}>
        <DetailGrid>
          <DetailField label={t("profile.replyStyle")}>{preferences?.reply_style || "--"}</DetailField>
          <DetailField label={t("profile.preferredTopics")}>{preferences?.preferred_topics?.join(", ") || "--"}</DetailField>
          <DetailField label={t("profile.avoidedTopics")}>{preferences?.avoided_topics?.join(", ") || "--"}</DetailField>
          <DetailField label={t("profile.sourceLabel")}>
            {preferencesOrigin ? <Badge variant={preferencesOrigin === "manual" ? "outline" : "secondary"}>{t(profileSourceKey(preferencesOrigin))}</Badge> : "--"}
          </DetailField>
        </DetailGrid>
      </DetailSection>
    </>
  );
}
