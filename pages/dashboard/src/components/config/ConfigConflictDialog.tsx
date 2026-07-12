import { RefreshCw } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";

export interface ConfigConflictDialogLabels {
  title: string;
  description: string;
  localChanges: string;
  remoteChanges: string;
  overlapChanges: string;
  loadRemote: string;
  reapplyLocal: string;
  waitingRemote: string;
  refreshRemote: string;
}

export interface ConfigConflictDialogProps {
  open: boolean;
  localPaths: readonly string[];
  remotePaths: readonly string[];
  overlapPaths: readonly string[];
  remoteReady: boolean;
  labels: ConfigConflictDialogLabels;
  onAcceptRemote: () => void;
  onRebaseRemote: () => void;
  onRefresh?: () => void;
}

interface PathGroupProps {
  id: string;
  label: string;
  paths: readonly string[];
}

function PathGroup({ id, label, paths }: PathGroupProps) {
  return (
    <section role="region" aria-labelledby={id} className="min-w-0">
      <h3 id={id} className="text-xs font-medium text-foreground">
        {label}
      </h3>
      <ul className="mt-2 flex min-w-0 flex-col gap-1.5">
        {paths.map((path) => (
          <li key={path} className="min-w-0 rounded-lg bg-muted px-2 py-1.5">
            <code className="block break-all text-xs text-muted-foreground">
              {path}
            </code>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ConfigConflictDialog({
  open,
  localPaths,
  remotePaths,
  overlapPaths,
  remoteReady,
  labels,
  onAcceptRemote,
  onRebaseRemote,
  onRefresh,
}: ConfigConflictDialogProps) {
  const id = useId();

  return (
    <Dialog open={open} onOpenChange={() => undefined}>
      <DialogContent
        showCloseButton={false}
        className="max-h-[calc(100vh-2rem)] max-w-xl overflow-hidden sm:max-w-xl"
      >
        <DialogHeader>
          <DialogTitle>{labels.title}</DialogTitle>
          <DialogDescription>{labels.description}</DialogDescription>
        </DialogHeader>

        <div
          data-testid="config-conflict-paths"
          className="flex max-h-64 min-w-0 flex-col gap-4 overflow-y-auto pr-1"
        >
          <PathGroup
            id={`${id}-local`}
            label={labels.localChanges}
            paths={localPaths}
          />
          <Separator />
          <PathGroup
            id={`${id}-remote`}
            label={labels.remoteChanges}
            paths={remotePaths}
          />
          <Separator />
          <PathGroup
            id={`${id}-overlap`}
            label={labels.overlapChanges}
            paths={overlapPaths}
          />
        </div>

        {!remoteReady ? (
          <div role="status" className="flex flex-col items-start gap-2">
            <p className="text-sm text-muted-foreground">
              {labels.waitingRemote}
            </p>
            {onRefresh ? (
              <Button type="button" variant="outline" onClick={onRefresh}>
                <RefreshCw data-icon="inline-start" />
                {labels.refreshRemote}
              </Button>
            ) : null}
          </div>
        ) : null}

        <DialogFooter className="rounded-b-lg">
          <Button
            type="button"
            variant="destructive"
            className="h-auto min-h-8 whitespace-normal sm:w-auto"
            disabled={!remoteReady}
            onClick={onAcceptRemote}
          >
            {labels.loadRemote}
          </Button>
          <Button
            type="button"
            className="h-auto min-h-8 whitespace-normal sm:w-auto"
            disabled={!remoteReady}
            onClick={onRebaseRemote}
          >
            {labels.reapplyLocal}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
