"""用户画像存储使用的固定 SQL 查询。"""

PROFILE_LIST_SQL = """
    SELECT * FROM user_profiles
    ORDER BY
      CASE WHEN :sort_by = 'user_id' AND :sort_order = 'asc'
           THEN user_id END COLLATE NOCASE ASC,
      CASE WHEN :sort_by = 'user_id' AND :sort_order = 'desc'
           THEN user_id END COLLATE NOCASE DESC,
      CASE WHEN :sort_by = 'display_name' AND :sort_order = 'asc'
           THEN display_name END COLLATE NOCASE ASC,
      CASE WHEN :sort_by = 'display_name' AND :sort_order = 'desc'
           THEN display_name END COLLATE NOCASE DESC,
      CASE WHEN :sort_by = 'total_messages' AND :sort_order = 'asc'
           THEN total_messages END ASC,
      CASE WHEN :sort_by = 'total_messages' AND :sort_order = 'desc'
           THEN total_messages END DESC,
      CASE WHEN :sort_by = 'total_sessions' AND :sort_order = 'asc'
           THEN total_sessions END ASC,
      CASE WHEN :sort_by = 'total_sessions' AND :sort_order = 'desc'
           THEN total_sessions END DESC,
      CASE WHEN :sort_by = 'first_seen_at' AND :sort_order = 'asc'
           THEN first_seen_at END ASC,
      CASE WHEN :sort_by = 'first_seen_at' AND :sort_order = 'desc'
           THEN first_seen_at END DESC,
      CASE WHEN :sort_by = 'last_seen_at' AND :sort_order = 'asc'
           THEN last_seen_at END ASC,
      CASE WHEN :sort_by = 'last_seen_at' AND :sort_order = 'desc'
           THEN last_seen_at END DESC,
      user_id COLLATE NOCASE ASC
    LIMIT :limit OFFSET :offset
"""
