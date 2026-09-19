-- revert-app-role-memory.sql - undo init-app-role-memory.sql.
--
-- ORDER MATTERS AND THIS FILE ENFORCES IT. The role cannot be dropped while anything is
-- still connected as it, and dropping it out from under a running openbrain-mcp turns a
-- reversible config change into an outage with a confusing cause. So: put DB_USER back to
-- `postgres` and recreate openbrain-mcp FIRST, then run this. If a backend is still
-- connected as ob_app_memory, this file refuses and names it rather than half-reverting.
--
--   psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f revert-app-role-memory.sql
--
-- Reverting is SAFE to skip: leaving `ob_app_memory` in place while every client connects
-- as `postgres` costs nothing - the role is inert without a client pointed at it. Revert
-- when you want the database back to its pre-promotion shape, not as an emergency step.
-- The emergency step is the compose change, and that one is enough on its own.

BEGIN;

DO $guard$
DECLARE
    n int;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory') THEN
        RAISE NOTICE 'ob_app_memory does not exist - nothing to revert.';
        RETURN;
    END IF;

    SELECT count(*) INTO n
      FROM pg_stat_activity
     WHERE usename = 'ob_app_memory' AND pid <> pg_backend_pid();

    IF n > 0 THEN
        RAISE EXCEPTION
          'revert-app-role-memory.sql REFUSES: % backend(s) are still connected as ob_app_memory. Point DB_USER back to postgres and recreate the client first, or this revert is an outage rather than a rollback.',
          n;
    END IF;
END
$guard$;

-- Ownership is not expected - the migration creates no objects - but a role that owns
-- anything cannot be dropped, and "DROP ROLE failed" is a worse diagnosis than this.
DO $owned$
DECLARE
    n int;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory') THEN
        RETURN;
    END IF;
    SELECT count(*) INTO n
      FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
     WHERE r.rolname = 'ob_app_memory';
    IF n > 0 THEN
        RAISE EXCEPTION
          'ob_app_memory owns % relation(s); reassign them before dropping the role.', n;
    END IF;
END
$owned$;

DO $drop$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory') THEN
        REVOKE ALL ON DATABASE openbrain FROM ob_app_memory;
        REVOKE service_role FROM ob_app_memory;
        -- Harmless if the optional personal-plane grant was never added.
        BEGIN
            REVOKE ob_plane_personal FROM ob_app_memory;
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
        DROP OWNED BY ob_app_memory;
        DROP ROLE ob_app_memory;
    END IF;
END
$drop$;

DO $verify$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory') THEN
        RAISE EXCEPTION 'revert-app-role-memory.sql ran but ob_app_memory still exists.';
    END IF;
END
$verify$;

COMMIT;
