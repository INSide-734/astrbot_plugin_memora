import {
  BarChart3,
  BookOpen,
  Brain,
  BrainCircuit,
  Clock,
  GitGraph,
  Heart,
  LayoutDashboard,
  MessageCircleCode,
  ScrollText,
  Search,
  Settings,
  SlidersHorizontal,
  StickyNote,
  UserRound,
  UsersRound,
  type LucideIcon,
} from "lucide-react";

import type { Translate } from "@/lib/i18n";
import type { PageId } from "@/types";

export type NavigationGroupId =
  | "overview"
  | "memory"
  | "insights"
  | "relationships"
  | "system";

type NavigationGroupLabelKey =
  | "nav.groupOverview"
  | "nav.groupMemory"
  | "nav.groupInsights"
  | "nav.groupRelationships"
  | "nav.groupSystem";

export interface DashboardNavigationItem {
  readonly id: PageId;
  readonly labelKey: `nav.${PageId}`;
  readonly icon: LucideIcon;
}

export interface DashboardNavigationGroup {
  readonly id: NavigationGroupId;
  readonly labelKey: NavigationGroupLabelKey;
  readonly items: readonly DashboardNavigationItem[];
}

export const DASHBOARD_NAVIGATION = [
  {
    id: "overview",
    labelKey: "nav.groupOverview",
    items: [{ id: "preview", labelKey: "nav.preview", icon: LayoutDashboard }],
  },
  {
    id: "memory",
    labelKey: "nav.groupMemory",
    items: [
      { id: "graph", labelKey: "nav.graph", icon: GitGraph },
      { id: "memory", labelKey: "nav.memory", icon: ScrollText },
      { id: "timeline", labelKey: "nav.timeline", icon: Clock },
      { id: "recall", labelKey: "nav.recall", icon: Search },
      { id: "injection", labelKey: "nav.injection", icon: SlidersHorizontal },
      { id: "knowledge", labelKey: "nav.knowledge", icon: BookOpen },
      { id: "notes", labelKey: "nav.notes", icon: StickyNote },
    ],
  },
  {
    id: "insights",
    labelKey: "nav.groupInsights",
    items: [
      {
        id: "intelligence",
        labelKey: "nav.intelligence",
        icon: BrainCircuit,
      },
      { id: "learning", labelKey: "nav.learning", icon: Brain },
      { id: "jargon", labelKey: "nav.jargon", icon: MessageCircleCode },
    ],
  },
  {
    id: "relationships",
    labelKey: "nav.groupRelationships",
    items: [
      { id: "profiles", labelKey: "nav.profiles", icon: UserRound },
      { id: "affection", labelKey: "nav.affection", icon: Heart },
      { id: "social", labelKey: "nav.social", icon: UsersRound },
    ],
  },
  {
    id: "system",
    labelKey: "nav.groupSystem",
    items: [
      { id: "system", labelKey: "nav.system", icon: BarChart3 },
      { id: "config", labelKey: "nav.config", icon: Settings },
    ],
  },
] as const satisfies readonly DashboardNavigationGroup[];

export function localizeDashboardNavigation(t: Translate) {
  return DASHBOARD_NAVIGATION.map((group) => ({
    ...group,
    label: t(group.labelKey),
    items: group.items.map((item) => ({
      ...item,
      label: t(item.labelKey),
    })),
  }));
}
