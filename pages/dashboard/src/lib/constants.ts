// ================================================================
// UI display constants — shared between production and mock paths.
// These are NOT mock data; they define the known set of mood types
// and relation categories the backend may return.
// ================================================================

export interface MoodTypeDef {
  type: string;
  label: string;
  label_en: string;
  color: string;
  emoji: string;
}

export const MOOD_TYPES: MoodTypeDef[] = [
  { type: "HAPPY", label: "开心", label_en: "Happy", color: "#f59e0b", emoji: "😊" },
  { type: "SAD", label: "难过", label_en: "Sad", color: "#6b7280", emoji: "😢" },
  { type: "EXCITED", label: "兴奋", label_en: "Excited", color: "#ef4444", emoji: "🤩" },
  { type: "CALM", label: "平静", label_en: "Calm", color: "#3b82f6", emoji: "😌" },
  { type: "ANGRY", label: "愤怒", label_en: "Angry", color: "#dc2626", emoji: "😠" },
  { type: "ANXIOUS", label: "焦虑", label_en: "Anxious", color: "#a855f7", emoji: "😰" },
  { type: "PLAYFUL", label: "调皮", label_en: "Playful", color: "#ec4899", emoji: "😜" },
  { type: "SERIOUS", label: "严肃", label_en: "Serious", color: "#1e293b", emoji: "🧐" },
  { type: "NOSTALGIC", label: "怀旧", label_en: "Nostalgic", color: "#f97316", emoji: "🥲" },
  { type: "CURIOUS", label: "好奇", label_en: "Curious", color: "#14b8a6", emoji: "🤔" },
];

export interface RelationCategoryDef {
  label: string;
  label_en: string;
}

export const RELATION_CATEGORIES: Record<string, RelationCategoryDef> = {
  blood: { label: "血缘", label_en: "Blood" },
  geographic: { label: "地缘", label_en: "Geographic" },
  career: { label: "职业", label_en: "Career" },
  emotional: { label: "情感", label_en: "Emotional" },
  interest: { label: "兴趣", label_en: "Interest" },
  intimacy: { label: "亲密度", label_en: "Intimacy" },
};
