import { useState, useEffect, useCallback } from "react";
import { UserRound, Tag, Trash2, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Checkbox } from "@/components/ui/checkbox";
import { dashboardLocale, formatDashboardDate, formatDashboardPercent } from "@/lib/i18n";

interface ProfilesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface Profile {
  user_id: string;
  display_name?: string;
  tag_count?: number;
  tags?: Array<{ name: string; confidence: number; category?: string }>;
  top_interests?: string[];
  preferences?: Record<string, unknown>;
  last_seen?: string;
  message_count?: number;
}

const PAGE_SIZE = 100;

export function ProfilesPage({ showToast }: ProfilesPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest(`profiles?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`));
      const nextProfiles = (res.profiles ?? res.items ?? []) as Profile[];
      const nextTotal = Number(res.total ?? nextProfiles.length);
      if (page > 0 && nextProfiles.length === 0 && nextTotal <= page * PAGE_SIZE) {
        setSelected(new Set());
        setTotal(nextTotal);
        setPage(Math.max(0, Math.ceil(nextTotal / PAGE_SIZE) - 1));
        return;
      }
      setProfiles(nextProfiles);
      setTotal(nextTotal);
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [page, showToast]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  const fetchDetail = async (userId: string) => {
    try {
      const res = unwrapApiData(await apiRequest(`profiles/detail?user_id=${userId}`));
      setDetail((res.profile ?? res) as Profile);
    } catch (e) { showToast(String(e), true); }
  };

  const deleteProfile = async (userId: string) => {
    try {
      await apiRequest("profiles/delete", { method: "POST", body: { user_id: userId } });
      showToast(t("toast.profileDeleted"));
      setDetail(null);
      fetchProfiles();
    } catch (e) { showToast(String(e), true); }
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };

  const toggleSelectAll = () => {
    setSelected(selected.size === profiles.length ? new Set() : new Set(profiles.map((p) => p.user_id)));
  };

  const batchDelete = async () => {
    if (!selected.size) return;
    try {
      await apiRequest("profiles/batch", { method: "POST", body: { user_ids: Array.from(selected), action: "delete" } });
      showToast(t("toast.batchDeleted", String(selected.size)));
      setSelected(new Set());
      fetchProfiles();
    } catch (e) { showToast(String(e), true); }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const changePage = (nextPage: number) => {
    setSelected(new Set());
    setPage(nextPage);
  };

  return (
    <PageFrame variant="dense" aria-label={t("nav.profiles")}>
      <PageHeader title={t("nav.profiles")} icon={<UserRound />} />

      <div className="flex min-h-12 shrink-0 items-center border-b bg-muted/30 px-4 py-2 sm:px-5 lg:px-6">
        <MetricGrid minItemWidth="8rem" className="w-full max-w-md gap-3">
          <div><div className="text-lg font-bold tabular-nums">{total}</div><div className="text-xs text-muted-foreground">{t("stats.profiles")}</div></div>
          <div><div className="text-lg font-bold tabular-nums">{profiles.reduce((s, p) => s + (p.tag_count ?? p.tags?.length ?? 0), 0)}</div><div className="text-xs text-muted-foreground">{t("table.tags")}</div></div>
        </MetricGrid>
      </div>

      <PageContent width="full" className="p-0">
        {loading ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("common.loading")}</p>
         : profiles.length === 0 ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("table.noData")}</p>
         : (
          <Table>
            <TableHeader className="sticky top-0 bg-background">
              <TableRow className="text-left text-xs font-medium uppercase text-muted-foreground">
                <TableHead className="w-10 px-4"><Checkbox aria-label={selected.size === profiles.length && profiles.length > 0 ? t("profiles.deselectAll") : t("profiles.selectAll")} checked={selected.size === profiles.length && profiles.length > 0} onCheckedChange={toggleSelectAll} /></TableHead>
                <TableHead className="px-4">{t("table.userId")}</TableHead>
                <TableHead>{t("table.name")}</TableHead>
                <TableHead>{t("table.tags")}</TableHead>
                <TableHead>{t("table.interests")}</TableHead>
                <TableHead>{t("table.lastSeen")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {profiles.map((p) => (
                <TableRow key={p.user_id} data-state={selected.has(p.user_id) ? "selected" : undefined} className="cursor-pointer text-sm"
                  onClick={() => fetchDetail(p.user_id)}>
                  <TableCell className="px-4" onClick={(ev) => ev.stopPropagation()}>
                    <Checkbox aria-label={t("profiles.selectProfile", p.display_name ?? p.user_id)} checked={selected.has(p.user_id)} onCheckedChange={() => toggleSelect(p.user_id)} />
                  </TableCell>
                  <TableCell className="px-4 font-mono text-xs text-muted-foreground">{p.user_id}</TableCell>
                  <TableCell className="font-medium"><Button variant="link" className="h-auto p-0 font-medium" aria-label={t("profiles.openProfile", p.display_name ?? p.user_id)} onClick={(event) => { event.stopPropagation(); fetchDetail(p.user_id); }}>{p.display_name ?? "--"}</Button></TableCell>
                  <TableCell><Badge>{p.tag_count ?? p.tags?.length ?? 0}</Badge></TableCell>
                  <TableCell className="max-w-xs"><div className="flex flex-wrap gap-1">{(p.top_interests ?? []).slice(0, 3).map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}</div></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDashboardDate(p.last_seen, locale)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </PageContent>

      <nav className="flex min-h-12 shrink-0 items-center justify-between border-t bg-background px-4 py-2 sm:px-5 lg:px-6" aria-label={t("profiles.pagination")}>
        <Button variant="outline" size="sm" aria-label={t("pagination.previousPage")} disabled={page === 0} onClick={() => changePage(Math.max(0, page - 1))}>{t("pagination.prev")}</Button>
        <span className="text-sm text-muted-foreground">{t("pagination.pageOf", String(page + 1), String(totalPages))}</span>
        <Button variant="outline" size="sm" aria-label={t("pagination.nextPage")} disabled={page + 1 >= totalPages} onClick={() => changePage(page + 1)}>{t("pagination.next")}</Button>
      </nav>

      {selected.size > 0 && (
        <PageToolbar className="border-b-0 border-t bg-muted/40 animate-slide-up">
          <span className="text-sm font-medium">{t("select.selected", String(selected.size))}</span>
          <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button>
        </PageToolbar>
      )}

      <Sheet open={detail !== null} onOpenChange={(open) => { if (!open) setDetail(null); }}>
        {detail && (
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("detail.profileOf", detail.display_name ?? detail.user_id)}</SheetTitle>
            <SheetDescription>{t("profiles.details")}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.userId")}</label><p className="font-mono text-sm">{detail.user_id}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.messages")}</label><p className="text-sm">{detail.message_count ?? "--"}</p></div>
            </div>
            {detail.tags && detail.tags.length > 0 && (
              <div><h4 className="text-xs font-semibold mb-2 flex items-center gap-1.5"><Tag size={12} /> {t("table.tags")}</h4>
                <div className="flex flex-col gap-1.5">
                  {detail.tags.map((t, i) => (
                    <div key={i} className="flex items-center justify-between rounded-lg bg-muted px-3 py-1.5 text-sm">
                      <span>{t.name}</span>
                      <div className="flex items-center gap-2">
                        <div className="h-1 w-16 rounded-full bg-border"><div className="h-1 rounded-full bg-primary" style={{ width: `${(t.confidence ?? 0) * 100}%` }} /></div>
                        <span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(t.confidence ?? 0, locale, { maximumFractionDigits: 0 })}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <SheetFooter>
            <Button variant="destructive" size="sm" onClick={() => deleteProfile(detail.user_id)}><Trash2 data-icon="inline-start" />{t("detail.deleteProfile")}</Button>
          </SheetFooter>
        </SheetContent>
        )}
      </Sheet>
    </PageFrame>
  );
}
