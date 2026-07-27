import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface EditConflictDialogProps {
  open: boolean;
  title: string;
  description: string;
  loadRemoteLabel: string;
  reapplyLocalLabel: string;
  onLoadRemote: () => void;
  onReapplyLocal: () => void;
}

export function EditConflictDialog({
  open,
  title,
  description,
  loadRemoteLabel,
  reapplyLocalLabel,
  onLoadRemote,
  onReapplyLocal,
}: EditConflictDialogProps) {
  return (
    <Dialog open={open} onOpenChange={() => undefined}>
      <DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="rounded-b-lg sm:flex-wrap">
          <Button type="button" variant="outline" onClick={onLoadRemote}>
            {loadRemoteLabel}
          </Button>
          <Button type="button" onClick={onReapplyLocal}>
            {reapplyLocalLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
