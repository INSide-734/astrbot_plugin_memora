import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/hooks/useI18n";
import { X } from "lucide-react";

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

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onCancel();
      }}
    >
      <DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("config.unsaved.title")}</DialogTitle>
          <DialogDescription>
            {t("config.unsaved.description")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="sm:flex-wrap">
          <Button
            type="button"
            variant="outline"
            className="h-auto min-w-0 max-w-full whitespace-normal break-words text-center"
            onClick={onCancel}
          >
            {t("config.unsaved.keepEditing")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="h-auto min-w-0 max-w-full whitespace-normal break-words text-center"
            onClick={onDiscard}
          >
            {t("config.unsaved.discard")}
          </Button>
        </DialogFooter>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute right-2 top-2"
          aria-label={t("common.close")}
          onClick={onCancel}
        >
          <X aria-hidden="true" />
        </Button>
      </DialogContent>
    </Dialog>
  );
}
