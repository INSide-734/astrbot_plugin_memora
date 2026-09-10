import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Info } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";

interface UpdateStatus {
  enabled?: boolean;
  available?: boolean;
  ignored?: boolean;
  current_version?: string;
  release?: {
    version?: string;
    notes?: string;
  } | null;
}

/**
 * 在 Dashboard 首次加载时检查一次更新，并以不影响页面布局的小型对话框展示发布说明。
 *
 * 仅显示更新信息，不复制 SystemPage 中的忽略、下载和安装操作；关闭后在当前页面会话内不再重开。
 */
export function GlobalUpdateDialog() {
  const { t } = useI18n();
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [open, setOpen] = useState(false);
  const checkStartedRef = useRef(false);
  const activeRef = useRef(false);

  useEffect(() => {
    activeRef.current = true;
    if (checkStartedRef.current) {
      return () => {
        activeRef.current = false;
      };
    }
    checkStartedRef.current = true;

    /** 读取可公开展示的更新摘要；网络失败不能阻断 Dashboard。 */
    const checkUpdate = async () => {
      try {
        const status = unwrapApiData<UpdateStatus>(await apiRequest("update/check", { retries: 0 }));
        if (
          activeRef.current
          && status.enabled !== false
          && status.available
          && !status.ignored
          && status.release?.version
        ) {
          setUpdateStatus(status);
          setOpen(true);
        }
      } catch {
        // 更新检查是旁路请求，失败时不影响管理面板。
      }
    };

    void checkUpdate();
    return () => {
      activeRef.current = false;
    };
  }, []);

  const currentVersion = updateStatus?.current_version ?? "--";
  const release = updateStatus?.release;
  const version = release?.version;
  if (!version) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="flex max-h-[calc(100vh-2rem)] min-h-0 w-full max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-md"
      >
        <DialogHeader className="shrink-0 border-b px-5 py-4">
          <div className="flex items-start gap-3">
            <Info className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
            <div className="min-w-0">
              <DialogTitle>{t("system.updateVersion", version)}</DialogTitle>
              <DialogDescription className="mt-1">
                {t("system.updateCurrent", currentVersion)}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto px-5 py-4">
          <h2 className="text-sm font-semibold text-foreground">{t("system.updateNotes")}</h2>
          {release.notes ? (
            <div className="mt-2 break-words text-sm text-muted-foreground [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2 [&_blockquote]:border-l [&_blockquote]:border-border [&_blockquote]:pl-3 [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mb-2 [&_h3]:mt-3 [&_h3]:font-semibold [&_li]:my-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-3 [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:p-2 [&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:p-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{release.notes}</ReactMarkdown>
            </div>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">{t("system.updateNoNotes")}</p>
          )}
        </div>
        <DialogFooter className="shrink-0">
          <Button type="button" onClick={() => setOpen(false)}>{t("common.close")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
