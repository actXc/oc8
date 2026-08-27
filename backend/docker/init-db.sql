-- Runs once on first cluster init (as the postgres superuser, against DB "oc8").
-- Sets up the two-role model required by the tenancy design (tech-spec ADR-002):
--   oc8_migrate — owns the schema, runs migrations (DDL).
--   oc8_app     — the runtime role; NO BYPASSRLS, so RLS policies always apply.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oc8_migrate') THEN
    CREATE ROLE oc8_migrate LOGIN PASSWORD 'oc8' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oc8_app') THEN
    CREATE ROLE oc8_app LOGIN PASSWORD 'oc8' NOBYPASSRLS;
  END IF;
END$$;

-- Migration role owns the public schema; app role may use it.
ALTER SCHEMA public OWNER TO oc8_migrate;
GRANT USAGE ON SCHEMA public TO oc8_app;

-- Future tables/sequences created by the migration role are usable by the app role
-- with DML only (never DDL). RLS still gates every row for oc8_app.
ALTER DEFAULT PRIVILEGES FOR ROLE oc8_migrate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oc8_app;
ALTER DEFAULT PRIVILEGES FOR ROLE oc8_migrate IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO oc8_app;
