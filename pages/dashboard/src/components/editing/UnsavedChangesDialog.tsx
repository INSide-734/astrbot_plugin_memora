import { Button } from "@/components/ui/Button";
import { X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface UnsavedChangesDialogProps {
  open: boolean;
  title: string;
  description: string;
  closeLabel?: string;
  keepEditingLabel: string;
  discardLabel: string;
  onKeepEditing: () => void;
  onDiscard: () => void;
}

export function UnsavedChangesDialog({
  open,
  title,
  description,
  closeLabel,
  keepEditingLabel,
  discardLabel,
  onKeepEditing,
  onDiscard,
}: UnsavedChangesDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onKeepEditing()}>
      <DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md">
        <DialogHeader className={closeLabel ? "pr-10" : undefined}>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          {closeLabel ? (
            <Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={closeLabel} onClick={onKeepEditing}>
              <X aria-hidden="true" />
            </Button>
          ) : null}
        </DialogHeader>
        <DialogFooter className="rounded-b-lg sm:flex-wrap">
          <Button type="button" variant="outline" className="h-auto min-h-8 min-w-0 whitespace-normal text-center" onClick={onKeepEditing}>
            {keepEditingLabel}
          </Button>
          <Button type="button" variant="destructive" className="h-auto min-h-8 min-w-0 whitespace-normal text-center" onClick={onDiscard}>
            {discardLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
