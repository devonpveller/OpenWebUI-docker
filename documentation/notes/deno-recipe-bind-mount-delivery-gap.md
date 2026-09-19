# A bind-mounted recipe change does not reach a long-running `deno run`

Recorded 2026-09-01, out of the U5 gmail-pull regression (DFU §C.10 exemption).
This is a **delivery** finding, not a code defect: it is why a correct fix sat on
disk through a scheduled run doing nothing, and it applies to every scheduled deno
recipe in OB1, not just the one that failed.

## What happened

| when (verified) | what |
|---|---|
| `2026-08-30 07:23Z` | `openbrain-gmail-pull` last started before the incident — the process that served every failing run loaded `pull-gmail.ts` at this moment |
| 2026-08-30 | U5's fail-closed `WITH CHECK` on `thoughts` starts refusing every INSERT from this producer with `42501` (it did not state its plane). Ingestion stops; the daily digest and the podcast have no mail. |
| `2026-08-31 19:23 -0400` | the stamping fix commits — OB1 `e9be2cd`, "the twelve DIRECT corpus producers state their plane". The parent gitlink is bumped. `git show -s --format=%ci e9be2cd`. |
| `2026-09-01 05:00Z` | the daily cron chain fires. **It fails again.** The corrected file is on disk, inside the container, at `/app/pull-gmail.ts`. The running process is still the one from 08-30. |
| `2026-09-01 10:24Z` | the container is recreated. The very next run writes 15 chunk rows, 0 errors; a 7-day backfill then writes 7 emails / 79 chunk rows, 0 errors. |

The outage ran two days. The part this note is about is the tail: **at least one
scheduled run failed with the correct code already sitting in the container**,
and nothing about the deployment said so.

## Why

`openbrain-gmail-pull` is not a script that is re-executed per run. From
`OB1/docker/docker-compose.scheduled.yml`:

    volumes:
      - ../recipes/email-history-import:/app
    entrypoint: ["deno", "run", "--allow-net", ..., "pull-gmail.ts"]

That is a **long-running HTTP service**: `deno run pull-gmail.ts` is PID 1, it
imports the entry module ONCE at process start, and then serves `POST /run` for
however long the container lives. `openbrain-cron` triggers runs over HTTP, so a
"run" is a function call inside a process that started days earlier.

The bind mount means the FILE on the host changed the moment the submodule moved.
The MODULE the running process holds did not, and never will. The container that
served the failing runs had started `2026-08-30T07:23` — before the fix existed.

Editing the file is not deploying it. `git submodule update` is not deploying it.
Bumping the parent gitlink is not deploying it. Only a new process is.

## Which services this covers

Every OB1 service whose entrypoint is `deno run <script>` against a bind-mounted
source directory and which then stays up serving HTTP. Read off
`OB1/docker/docker-compose.scheduled.yml` on 2026-09-01 — the whole deno tier
qualifies, five for five:

| service | mount | entry module |
|---|---|---|
| `openbrain-digest` | `../recipes/daily-digest:/app` | `send-digest.ts` |
| `openbrain-gmail-pull` | `../recipes/email-history-import:/app` | `pull-gmail.ts` |
| `openbrain-gmail-prune` | `../recipes/email-history-import:/app` | `prune-short-term.ts` |
| `openbrain-podcast` | `../recipes/daily-digest:/app` | `podcast-server.ts` |
| `openbrain-idea-refinery` | `../integrations/openbrain-idea-refinery:/app` | `index.ts` |

`openbrain-cron` is the near-miss in that tier: supercronic DOES re-read its
bind-mounted crontab, but only on `docker compose restart openbrain-cron`, which
its own header says. Same class of gap, already documented there.

Re-check the compose file rather than trusting this table; the property to look
for is *bind mount + long-lived process*, not the service name.

Not covered: anything invoked as a fresh `docker run`/`docker compose run` per
job, or built into an image (an image change forces a recreate anyway).

## What to do

**Any recipe fix that ships by moving the submodule needs a container recreate to
take effect:**

    docker compose -f OB1/docker/docker-compose.yml -f OB1/docker/docker-compose.scheduled.yml \
      up -d --force-recreate <service>

`restart` is enough to re-exec the entrypoint, but `--force-recreate` is the
habit worth keeping — it also picks up compose/env changes, which a `restart`
does not.

Then prove the new code is the code that ran: trigger one run and look for
something only the new version emits. For this recipe that is now the
`RUN STATUS:` line, which every finished run prints.

## The general shape

The failure mode is not "we forgot to restart". It is that **three different
artefacts all look like "the fix is deployed"** — the working tree, the submodule
pointer, and the container's own `docker ps` uptime (which shows a healthy,
running, *stale* process) — and none of them is the one that matters. The only
evidence that a recipe change is live is output that the old code could not have
produced.

Related: the podcast JSON regression (2026-08-29) died the same death from the
other direction — a hot-patch inside a running container that no rebuild
reproduced. Same lesson, opposite sign: **the running process and the repository
are two different claims about what the code is, and they have to be reconciled
deliberately.**
