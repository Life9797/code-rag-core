-- eval runs and per-query results (§35); one run per job.
CREATE TABLE plugin_eval.runs (
    job_id       uuid PRIMARY KEY,
    project_id   uuid NOT NULL,
    golden_sha   text NOT NULL,
    summary      jsonb,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
CREATE TABLE plugin_eval.results (
    job_id      uuid NOT NULL REFERENCES plugin_eval.runs ON DELETE CASCADE,
    query_id    text NOT NULL,
    lang        text NOT NULL,
    hits        jsonb NOT NULL,
    recall      numeric NOT NULL,
    hit         numeric NOT NULL,
    rr          numeric NOT NULL,
    latency_ms  numeric NOT NULL,
    PRIMARY KEY (job_id, query_id, lang)
);
