import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { useI18n } from "@/hooks/useI18n";

interface ConfigUnsavedDialogProps {
  open: boolean;
  onCancel: () => void;
  onDiscard: () => void;
}

export function ConfigUnsavedDialog({
  open,
  onCancel,
  onDiscard,
}: ConfigUnsavedDialogProps) {
  const { t } = useI18n();

  return <UnsavedChangesDialog open={open} title={t("config.unsaved.title")} description={t("config.unsaved.description")} keepEditingLabel={t("config.unsaved.keepEditing")} discardLabel={t("config.unsaved.discard")} onKeepEditing={onCancel} onDiscard={onDiscard} />;
}
