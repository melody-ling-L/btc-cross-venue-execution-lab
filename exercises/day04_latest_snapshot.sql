-- 学习者提交的选择语句，助手仅整理排版。
SELECT snapshot_id
FROM snapshots
WHERE is_complete = 1
ORDER BY snapshot_time DESC
LIMIT 1;
