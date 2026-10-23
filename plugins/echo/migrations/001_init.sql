-- echo plugin's own table (I-P2: Core never reads it).
CREATE TABLE plugin_echo.calls (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id uuid NOT NULL,
    at      timestamptz NOT NULL DEFAULT now()
);
