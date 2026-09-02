-- init-app-role-memory.sql - the agent plane's door stops being a superuser.
--
-- DFU section C.8 clause 3. The live run found the personal-plane boundary OPEN at two
-- doors (door-openbrain-mcp-door, door-cloud-search-thoughts), both returning the personal
-- fixture with HTTP 200. Neither is a policy bug: openbrain-mcp connects as `postgres`, and
-- RLS binds no role with BYPASSRLS. Every predicate in the 180/190 chain is inert in front
-- of that connection.
--
-- WHY ONE ROLE AND NOT NINE. `openbrain-gateway` (cloud) and `openbrain-ops-gateway` (ops)
-- hold no database connection at all - both are HTTP proxies whose OPENBRAIN_URL is
-- http://openbrain-mcp:8000 (measured 2026-09-01). All three of clause 3's MCP doors
-- therefore traverse ONE connection, and moving that one connection off `postgres` closes
-- all three. H1's larger promotion (the eight `ob_app` clients, the PostgREST
-- authenticator) is a separate, larger change that clause 3 does not require and this file
-- deliberately does not make.
--
-- THIS REUSES H1's SCHEME, it does not invent a second one: the role name, the membership
-- in `service_role`, and the treatment of `ob_plane_personal` are H1's
-- (documentation/implementation-guide/dark-factory-unification/H1-APP-ROLE-PROMOTION.md).
-- H1's own OB1/docker/init-app-role.sql is NOT in this checkout and is on no reachable
-- commit of either repository - see section 6 of the promotion plan. This file is that
-- design re-materialised at the width clause 3 needs, in a repository a clean clone can
-- reproduce.
--
-- IDEMPOTENT. Safe to re-run. It sets no password: passwords never enter git. Apply, then
-- ALTER ROLE ... PASSWORD separately (see the promotion plan).
--
--   psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f init-app-role-memory.sql
--
-- Revert: revert-app-role-memory.sql, beside this file.

BEGIN;

-- ---------------------------------------------------------------------------------------
-- 0. PRECONDITIONS. Refuse rather than half-apply.
--
-- A role that inherits `service_role` is bound by the ops-plane policies ONLY IF those
-- policies exist and row security is FORCED on the tables they guard. Applied to a database
-- where the 180/190 chain has not run, this file would produce a non-superuser role with no
-- boundary in front of it and no error - a promotion that reports success and closes
-- nothing. Every assertion below is something this migration's correctness depends on.
-- ---------------------------------------------------------------------------------------
DO $precheck$
DECLARE
    missing text := '';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        missing := missing || ' role:service_role';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_plane_personal') THEN
        missing := missing || ' role:ob_plane_personal';
    END IF;

    -- Row security must be ENABLED **and FORCED**. Without FORCE the table owner is exempt,
    -- and more to the point an unforced table is a boundary that depends on who owns it.
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname IN ('thoughts', 'agent_memories')
           AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
    ) THEN
        missing := missing || ' forced-rls:thoughts/agent_memories';
    END IF;

    -- The policies this role will actually be bound by. Named individually: a role that
    -- inherits `service_role` into a database with no `TO service_role` policy reads
    -- NOTHING (default deny), which looks like a closed boundary and is really an outage.
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public' AND tablename = 'thoughts'
                      AND policyname = 'thoughts_ops_plane') THEN
        missing := missing || ' policy:thoughts_ops_plane';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public' AND tablename = 'agent_memories'
                      AND policyname = 'agent_memories_ops_plane') THEN
        missing := missing || ' policy:agent_memories_ops_plane';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION
          'init-app-role-memory.sql REFUSES to apply: the boundary it puts this role behind is not present:%. Apply the 180/190 agent-memory RLS chain first.',
          missing;
    END IF;
END
$precheck$;

-- ---------------------------------------------------------------------------------------
-- 1. THE ROLE.
--
-- INHERIT is required, not incidental: the ops-plane policies are written `TO service_role`,
-- and PostgreSQL applies a policy to the current user only through membership it INHERITS.
-- A NOINHERIT role here would be default-denied on every table and the door would return an
-- empty corpus - indistinguishable, from outside, from a boundary working perfectly.
--
-- NOSUPERUSER NOBYPASSRLS are the entire point, and are asserted again in section 3.
-- ---------------------------------------------------------------------------------------
DO $mkrole$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory') THEN
        CREATE ROLE ob_app_memory
            LOGIN INHERIT
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    ELSE
        ALTER ROLE ob_app_memory
            LOGIN INHERIT
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$mkrole$;

-- The ops plane, and nothing else.
GRANT service_role TO ob_app_memory;

-- ---------------------------------------------------------------------------------------
-- 2. THE PERSONAL PLANE IS DELIBERATELY *NOT* GRANTED.
--
-- H1 provisioned `ob_app_memory` with a switchable `ob_plane_personal` membership for a
-- future per-request chokepoint in openbrain-mcp. That chokepoint DOES NOT EXIST:
-- openbrain-mcp issues no `SET ROLE` anywhere in its source. Granting a path no code takes
-- only widens what a leaked password reaches, so this file leaves it ungranted and section
-- 3 ASSERTS the absence - if a later item adds the chokepoint, it adds the grant with it,
-- in the same change that makes it used.
--
-- When that item lands, this is the grant it wants (PG16+), and it must stay INHERIT FALSE
-- or the role sits on the personal plane always rather than when it says so:
--
--     GRANT ob_plane_personal TO ob_app_memory WITH INHERIT FALSE, SET TRUE;
--
-- If that is ever added, revoke it in revert-app-role-memory.sql too.
-- ---------------------------------------------------------------------------------------
REVOKE ob_plane_personal FROM ob_app_memory;

GRANT CONNECT ON DATABASE openbrain TO ob_app_memory;

-- ---------------------------------------------------------------------------------------
-- 3. ASSERT WHAT WAS JUST BUILT, in the transaction that built it.
--
-- Each of these is a way this migration could "succeed" and leave the door open.
-- ---------------------------------------------------------------------------------------
DO $verify$
DECLARE
    bad text := '';
BEGIN
    -- The claim itself.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'ob_app_memory' AND (rolsuper OR rolbypassrls)) THEN
        bad := bad || ' ob_app_memory is still superuser or bypassrls (RLS cannot bind it);';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ob_app_memory' AND rolcanlogin) THEN
        bad := bad || ' ob_app_memory cannot log in, so nothing can connect as it;';
    END IF;

    -- Bound by the ops plane...
    IF NOT pg_has_role('ob_app_memory', 'service_role', 'USAGE') THEN
        bad := bad || ' ob_app_memory does not INHERIT service_role, so every ops-plane policy default-denies it;';
    END IF;

    -- ...and off the personal plane, by both tests that matter. USAGE is "does the policy
    -- apply to it"; MEMBER is "can it SET ROLE into it". A grant that fails only the second
    -- is the silent one.
    IF pg_has_role('ob_app_memory', 'ob_plane_personal', 'USAGE') THEN
        bad := bad || ' ob_app_memory INHERITS ob_plane_personal - the personal policies apply to it and the door is still open;';
    END IF;
    IF pg_has_role('ob_app_memory', 'ob_plane_personal', 'MEMBER') THEN
        bad := bad || ' ob_app_memory can SET ROLE ob_plane_personal, which no code path needs;';
    END IF;

    -- No predefined role can undo all of the above in one line.
    IF EXISTS (SELECT 1 FROM pg_auth_members m
                 JOIN pg_roles r ON r.oid = m.roleid
                 JOIN pg_roles g ON g.oid = m.member
                WHERE g.rolname = 'ob_app_memory' AND r.rolname LIKE 'pg' || chr(92) || '_%') THEN
        bad := bad || ' ob_app_memory holds a pg_* predefined role (pg_read_all_data undoes this file);';
    END IF;

    IF bad <> '' THEN
        RAISE EXCEPTION 'init-app-role-memory.sql built a role that does not hold the boundary:%', bad;
    END IF;
END
$verify$;

COMMIT;

-- Reminder, deliberately outside the transaction: this role has NO PASSWORD yet and cannot
-- authenticate until one is set. That is the safe order - the role exists and is inert
-- until the operator gives it a credential and points a client at it.
