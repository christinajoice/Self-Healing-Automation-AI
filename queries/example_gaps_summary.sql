-- queries/example_gaps_summary.sql
-- Validates the Gaps column in the Network Adequacy UI table.
--
-- Parameters (injected from UI or CSV):
--   :network_group  — active network filter value
--   :plan_type      — active plan type filter value
--   :state          — selected state (optional)
--
-- Rename this file to  gaps_summary.sql  and update the query
-- to match your actual schema and table names.

SELECT
    s.state_name,
    COUNT(DISTINCT na.county_id || '-' || na.specialty_id) AS gaps
FROM network_adequacy na
JOIN states s ON s.id = na.state_id
WHERE na.network_group = :network_group
  AND na.plan_type     = :plan_type
  AND na.is_gap        = true
GROUP BY s.state_name
ORDER BY s.state_name
