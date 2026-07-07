import { useState, useEffect, useCallback } from "react";
import { apiRequest, unwrapApiData } from "@/lib/bridge";

export interface GroupInfo {
  group_id: string;
  source?: string;
  message_count?: number;
}

interface UseGroupsReturn {
  groups: GroupInfo[];
  groupId: string;
  setGroupId: (id: string) => void;
  loading: boolean;
  refresh: () => void;
}

/**
 * Shared hook for group list fetching and selection.
 * Used by AffectionPage, JargonPage, SocialPage (and any future
 * group-scoped page) to avoid duplicating the same fetch logic.
 */
export function useGroups(): UseGroupsReturn {
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [groupId, setGroupId] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchGroups = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("groups"));
      const list = (res.groups ?? []) as GroupInfo[];
      setGroups(list);
      if (list.length > 0) {
        setGroupId((prev) => {
          // Keep current selection if it still exists in the fetched list;
          // otherwise fall back to the first group. If the previously
          // selected group was deleted, this prevents orphaned API calls.
          const exists = list.some((g) => g.group_id === prev);
          return exists ? prev : list[0].group_id;
        });
      } else {
        setGroupId("");
      }
    } catch {
      // groups list is non-critical — page still works with empty selector
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGroups();
  }, [fetchGroups]);

  return { groups, groupId, setGroupId, loading, refresh: fetchGroups };
}
