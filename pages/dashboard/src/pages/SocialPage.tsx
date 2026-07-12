import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { UsersRound, RefreshCw, ArrowRightLeft, Tag } from "lucide-react";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/Progress";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RELATION_CATEGORIES } from "@/lib/constants";
import { dashboardLocale, formatDashboardPercent } from "@/lib/i18n";
import type { SocialRelationEntry } from "@/types";

interface SocialPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

export function SocialPage({ showToast }: SocialPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const { groups, groupId, setGroupId } = useGroups();
  const [relations, setRelations] = useState<SocialRelationEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState("all");

  const fetchRelations = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const params = [`group_id=${groupId}`];
      if (category !== "all") params.push(`category=${category}`);
      const res = unwrapApiData(await apiRequest(`social/relations?${params.join("&")}`));
      setRelations((res.relations ?? []) as SocialRelationEntry[]);
    } catch (e) { showToast(String(e), true); }
    finally { setLoading(false); }
  }, [groupId, category, showToast]);

  useEffect(() => { fetchRelations(); }, [fetchRelations]);

  const relationLabel = (type: string): string => {
    const key = `relation.${type}`;
    const translated = t(key);
    // fallback: if the key wasn't translated, return the raw type
    return translated !== key ? translated : type;
  };

  const categories = [
    { value: "all", label: t("social.allCategories") },
    ...Object.keys(RELATION_CATEGORIES).map((value) => ({ value, label: t(`social.category.${value}`) })),
  ];

  const relationTable = loading ? (
    <p className="py-12 text-center text-sm text-muted-foreground">{t("table.loading")}</p>
  ) : relations.length === 0 ? (
    <p className="py-12 text-center text-sm text-muted-foreground">{t("social.noData")}</p>
  ) : (
    <Card className="gap-0 py-0">
      <Table>
        <TableHeader className="sticky top-0 bg-background"><TableRow>
          <TableHead>{t("social.relations")}</TableHead>
          <TableHead>{t("social.category")}</TableHead>
          <TableHead>{t("social.strength")}</TableHead>
          <TableHead>{t("social.frequency")}</TableHead>
          <TableHead>{t("table.tags")}</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {relations.map((r) => (
            <TableRow key={`${r.from_user}-${r.to_user}-${r.relation_type}`}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{r.from_user}</span>
                  <ArrowRightLeft />
                  <span className="text-xs font-medium">{r.to_user}</span>
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">{relationLabel(r.relation_type)}</div>
              </TableCell>
              <TableCell><Badge variant="secondary">{RELATION_CATEGORIES[r.category] ? t(`social.category.${r.category}`) : r.category}</Badge></TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <Progress aria-label={`${r.from_user} → ${r.to_user} ${relationLabel(r.relation_type)} ${t("social.strength")}`} value={r.strength} className="h-1.5 w-20" />
                  <span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(r.strength, locale, { maximumFractionDigits: 0 })}</span>
                </div>
              </TableCell>
              <TableCell className="text-xs tabular-nums text-muted-foreground">{r.frequency}</TableCell>
              <TableCell>
                <div className="flex flex-wrap items-center gap-1">
                  {r.tags.map((tag) => <Badge key={tag} variant="outline"><Tag data-icon="inline-start" />{tag}</Badge>)}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );

  return (
    <PageFrame variant="standard" aria-label={t("social.title")}>
      <PageHeader
        title={t("social.title")}
        icon={<UsersRound />}
        actions={<>
          <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
            <SelectTrigger className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
            <SelectContent>
              <SelectGroup>
              {groups.length > 0 ? groups.map((g) => (
                <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}{g.message_count ? ` (${g.message_count})` : ""}</SelectItem>
              )) : (
                <SelectItem value="loading">—</SelectItem>
              )}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={fetchRelations}><RefreshCw data-icon="inline-start" />{t("common.refresh")}</Button>
        </>}
      />
      <Tabs value={category} onValueChange={setCategory} className="min-h-0 flex-1 gap-0">
        <PageToolbar className="flex-nowrap overflow-x-auto bg-background">
          <TabsList variant="line" aria-label={t("social.category")} className="h-9 min-w-max">
            {categories.map((item) => <TabsTrigger key={item.value} value={item.value} className="px-3 text-xs">{item.label}</TabsTrigger>)}
          </TabsList>
        </PageToolbar>
        {categories.map((item) => (
          <TabsContent key={item.value} value={item.value} className="min-h-0 overflow-auto">
            <PageContent>{relationTable}</PageContent>
          </TabsContent>
        ))}
      </Tabs>
    </PageFrame>
  );
}
