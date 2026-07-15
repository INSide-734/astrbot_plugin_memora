import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";

export interface UnsavedChangesDialogProps {
  open: boolean;
  title: string;
  description: string;
  keepEditingLabel: string;
  discardLabel: string;
  onKeepEditing: () => void;
  onDiscard: () => void;
}

export function UnsavedChangesDialog({
  open,
  title,
  description,
  keepEditingLabel,
  discardLabel,
  onKeepEditing,
  onDiscard,
}: UnsavedChangesDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onKeepEditing()}>
      <DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="rounded-b-lg sm:flex-wrap">
          <Button type="button" variant="outline" onClick={onKeepEditing}>
            {keepEditingLabel}
          </Button>
          <Button type="button" variant="destructive" onClick={onDiscard}>
            {discardLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
