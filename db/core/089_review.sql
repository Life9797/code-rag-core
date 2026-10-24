-- Review fixes 2026-10-24. Webhook allowlist entries match at a URL boundary only (https://a.com no longer matches
-- https://a.com.evil.org/).
CREATE OR REPLACE FUNCTION core.webhook_allowed(_url text, _tenant uuid, _project uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT _url ~ '^https://[^/@]+(/|$)' AND EXISTS (
        SELECT 1 FROM regexp_split_to_table(coalesce(core.policy_value('core.webhook_allowlist',
                          ARRAY[_project::text, _tenant::text]) #>> '{}', ''), '[\s,]+') p
        WHERE p <> '' AND left(_url, length(p)) = p
          AND (right(p, 1) = '/' OR length(_url) = length(p) OR substr(_url, length(p) + 1, 1) IN ('/', '?', '#'))) $$;
