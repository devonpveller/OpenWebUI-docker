# Config + secrets backup (encrypted)

Every gitignored file the stack cannot run or be recovered without - every plane's
`.env`, `secrets/`, `portal/config/authelia/users_database.yml`, the OAuth token files
and other credentials bind-mounted into containers - is archived **every night**,
**encrypted with [age](https://age-encryption.org)** to the operator's **public** key,
into `backups/config-secrets/`. The weekly NAS sync mirrors `backups/` as it always has,
so the archives go off-site with no change there.

Why it exists: on 2026-10-04 Authelia crash-looped 205 times because
`users_database.yml` was gone - and had never been backed up. The sidecars back up
volumes; nothing backed up these files, and `check-backup-coverage.ps1` said CLEAN
because it only looked at volumes. It now fails on any such file that is not in the
newest archive.

| Piece | Where |
|---|---|
| the job | `scripts/backup/config_secrets_backup.py` (`run`, `check`, `inventory`, `pin-ignores`) |
| the policy (what counts, retention, the pinned age build) | `scripts/backup/config-secrets.toml` |
| the schedule | Windows task `\AI-Stack\AI-Stack Config Secrets Backup`, daily 02:30 local (`scripts/backup/install-config-secrets-task.ps1`) |
| the public key (the ONLY key on the host) | `secrets/config-backup/age-recipients.txt` |
| the private key | **offline, with the operator** - never on the host |
| output | `backups/config-secrets/config-secrets-<UTC>.tar.age` + `.sha256` + `.files.txt` |
| log | `logs/config-secrets-backup-<date>.log` |
| alerts | `scripts/sysadmin-mcp/check_backups.py` (daily, #sysadmin) and the watchdog's backup-recency row |
| coverage gate | `scripts/checks/check-backup-coverage.ps1` (its "Config + secrets coverage" section) |

## How it works

1. **Inventory, derived** (`python scripts/backup/config_secrets_backup.py inventory`
   prints it - paths and why, never a value): every untracked `.env`-named file, every
   untracked file under `secrets/`, `OB1/secrets/`, `agent-org/agent-bridge/secrets/`,
   every untracked file a compose bind mount brings in (each plane whose own `.env`
   exists is rendered with `docker compose config` - the CLI is enough, no daemon - with
   no profile, with this host's profiles, and with each declared profile), plus the
   policy's `[[include]]` and `[[required]]` entries. A new plane `.env` or a new
   bind-mounted credential file is picked up with no edit. A **required** file that is
   absent - or is an EMPTY DIRECTORY, which is what Docker leaves when a bind-mounted
   file is missing - is a `MISSING` error.
2. **Encryption without plaintext on disk**: the tar is built in memory and streamed
   into `age --encrypt --recipients-file ...`; age writes only ciphertext, to
   `<name>.partial`, renamed when age succeeded and the output starts with the age
   header. Every file is capped at `max_file_bytes` (5 MiB).
3. **Why age, and why the official binary**: age is small, audited and has one job; a
   restore needs nothing but `age` and `tar`. The job runs the official release binary,
   pinned by sha256 in the policy (`[[age_pin]]`), and refuses any other build - that
   binary sees every secret on its stdin. A container per run would add the Docker
   daemon as a dependency of the one backup that must work when Docker is broken, and
   re-implementing age in Python would mean shipping our own crypto.
4. **Retention**: the newest `retain_count` (14) archives, by count like every sidecar -
   **except** that an older archive is kept while it holds the last copy of a file the
   newer archives no longer have (a deleted secret is never pruned out of its last
   backup by the clock).
5. **Reporting**: a run that completes logs `=== config-secrets backup complete ===`.
   A run with a problem still writes the archive (everything else is worth keeping)
   but exits 1 without that marker; `check_backups.py` then reports
   `config-secrets - last run did NOT complete. Errors: MISSING <file> ...` the next
   morning even though the archive is fresh. No archive for 36 h (the watchdog: 52 h)
   is stale too.
6. **pin-ignores**: each run writes the archived, currently-gitignored paths into the
   repo's `.git/info/exclude` (a marked block). That file is not versioned, so it applies
   on every branch: checking out an OLD branch whose `.gitignore` predates a rule can no
   longer turn a live secret into an "untracked change" that a GUI client then stashes
   away - which is how `users_database.yml` was lost on 2026-09-28.
   **Only the main checkout writes that block.** `info/exclude` lives in the common git
   directory and is shared by every worktree; a run from a linked worktree logs
   `pin-ignores skipped: ... is a linked worktree` and leaves the file alone, so it can
   never drop the main checkout's pins. Lines outside the marked block are never changed
   and the file's line endings are kept; a file that is not UTF-8 is not rewritten and the
   run fails with an `[ERROR] pin-ignores FAILED` line.
7. **Links are not followed**: a symlink or junction where a secret would be archived
   (under a secret dir, named like an env file, matching an include) or a bind-mounted
   link is a `LINK` problem - never archived, never silently skipped.

Exit codes of `run`: 0 complete; 1 archive written but a problem was logged; 2 refused
(no recipient, a private key in the recipients file, an unpinned age binary, a bad
policy - nothing written); 3 the archive could not be written.

## Threat model, plainly

- A leak of the NAS or of `backups/` reveals **which** files exist (`.files.txt`, the
  tar member names inside are encrypted) and their approximate total size - nothing
  else. `.files.txt` holds no hashes on purpose: the hash of a short secret is
  guessable.
- The host holds only the public key. Someone with the host can make NEW archives but
  cannot read old ones.
- **If the private key is lost, every one of these archives is unreadable. There is no
  recovery.** Keep two copies, offline, in different places.

## One-time setup (operator)

Run from the repo root in PowerShell unless it says Git Bash.

### 1. Get the pinned age release

Download `age-v1.3.2-windows-amd64.zip` from
<https://github.com/FiloSottile/age/releases/tag/v1.3.2>, check it, and unpack it
OUTSIDE the repo:

```powershell
(Get-FileHash .\age-v1.3.2-windows-amd64.zip -Algorithm SHA256).Hash.ToLower()
# must print: f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7
Expand-Archive .\age-v1.3.2-windows-amd64.zip -DestinationPath C:\Tools
(Get-FileHash C:\Tools\age\age.exe -Algorithm SHA256).Hash.ToLower()
# must print: 2821a4ed191da07372acd302e5f6feae7a7985e285e1417765ebe74025af45f0
```

(A different build is refused by the job until its sha256 is added as an `[[age_pin]]`
in `scripts/backup/config-secrets.toml`, deliberately, in a commit.)

### 2. Generate the key pair - the private key goes straight to offline media

Plug in the offline medium (here `E:`) and write the private key **there**, never to
the host's disk:

```powershell
C:\Tools\age\age-keygen.exe -o E:\ai-stack-config-backup.key
# prints: Public key: age1....
```

Optional, recommended: protect the key file with a passphrase, then delete the plain one
(a restore then asks for the passphrase):

```powershell
C:\Tools\age\age.exe --passphrase -o E:\ai-stack-config-backup.key.age E:\ai-stack-config-backup.key
Remove-Item E:\ai-stack-config-backup.key
```

Make the second copy now (another medium, or the key line on paper in a safe place).
**Without the private key the archives are unreadable. Nobody can recover it.**

### 3. Install the public key on the host

```powershell
New-Item -ItemType Directory -Force secrets\config-backup | Out-Null
Set-Content -Encoding ascii secrets\config-backup\age-recipients.txt "age1...the public key printed above..."
```

One recipient per line; a second line (a second key pair, kept elsewhere) is allowed and
either private key then decrypts. The job refuses the file if it ever contains a
private key (`AGE-SECRET-KEY-...`), and refuses a file that starts with a byte-order
mark - write it with `-Encoding ascii` exactly as above (PowerShell 5.1's `-Encoding utf8`
and `unicode` both add a BOM).

### 4. First run by hand, and prove the round trip

```powershell
python scripts\backup\config_secrets_backup.py run --age C:\Tools\age\age.exe
```

It must end with `=== config-secrets backup complete ===`. If it lists `MISSING`
files, restore them first (section "Restore one file") - the archive is still written.
Then prove the offline key opens it (Git Bash; the plug-in medium is `/e`):

```sh
F=$(ls -1 backups/config-secrets/config-secrets-*.tar.age | sort | tail -1)
/c/Tools/age/age.exe --decrypt -i /e/ai-stack-config-backup.key "$F" | tar -tvf -
```

The listing must match `"$F.files.txt"`.

### 5. Schedule it (elevated PowerShell)

```powershell
.\scripts\backup\install-config-secrets-task.ps1 -AgePath C:\Tools\age\age.exe -RunNow
Get-Content .\logs\config-secrets-backup-$(Get-Date -Format yyyy-MM-dd).log -Tail 5
```

### 6. Confirm the gates, then unplug the key

```powershell
.\scripts\checks\check-backup-coverage.ps1     # "config+secrets coverage: CLEAN"
python scripts\sysadmin-mcp\check_backups.py --check
```

### Rotating the key

Generate a new pair (step 2), replace the line in `age-recipients.txt` (step 3). Keep the
OLD private key until every archive made with it is gone from `backups/` and from both
NAS slots (14 nights + 2 weeks).

## Restore

Restores happen where the private key is: plug the offline medium into the host (or
copy the archive to the machine that holds the key). All commands are Git Bash, from the
repo root; Git Bash pipes are binary-safe (Windows PowerShell 5.1 pipes between native
programs are NOT - do not pipe `age` into `tar` there).

### 0. Pick and verify the archive

Local, or from the NAS (`\\<nas>\...\slot-A\config-secrets\` or `slot-B`):

```sh
F=$(ls -1 backups/config-secrets/config-secrets-*.tar.age | sort | tail -1)   # or pick an older one
( cd "$(dirname "$F")" && sha256sum -c "$(basename "$F").sha256" )          # must say: OK
cat "$F.files.txt"                                                            # is the file you need in it?
```

Need a file the newest archive no longer has (it was deleted before the last run)?
`grep -l <path> backups/config-secrets/*.files.txt` names the archives that still hold it.

Set the key path once (with a passphrase-protected key, age asks for the passphrase):

```sh
AGE=/c/Tools/age/age.exe
KEY=/e/ai-stack-config-backup.key        # or /e/ai-stack-config-backup.key.age
```

### Restore one file (example: `users_database.yml`)

Extract into a scratch folder, never straight over the live file:

```sh
R=$(mktemp -d)
"$AGE" --decrypt -i "$KEY" "$F" | tar -xf - -C "$R" portal/config/authelia/users_database.yml
ls -l "$R/portal/config/authelia/users_database.yml"
```

Then put it in place. For `users_database.yml` specifically: if Authelia started while
the file was missing, Docker created an empty DIRECTORY with that name - stop Authelia,
remove that empty directory, copy the file, start Authelia again:

```sh
docker stop authelia
[ -d portal/config/authelia/users_database.yml ] && rmdir portal/config/authelia/users_database.yml
cp "$R/portal/config/authelia/users_database.yml" portal/config/authelia/users_database.yml
docker start authelia
rm -rf "$R"
```

(`rmdir` only removes an EMPTY directory, so it cannot delete anything by mistake.)
For a plane `.env`, recreate that plane's containers afterwards
(`python scripts/stack/stack.py restart <plane>`; the portal is `manual` -
`scripts/portal/portal-off.ps1` then `portal-on.ps1`), since a container reads its env
at creation.

### Restore everything (rebuilt host)

```sh
R=$(mktemp -d)
"$AGE" --decrypt -i "$KEY" "$F" | tar -xf - -C "$R"
find "$R" -type f | sed "s#^$R/##"         # review what came back
cp -r "$R"/. .                             # into the checkout (overwrites same-named files)
rm -rf "$R"
```

Then bring the stack up as usual (`python scripts/stack/stack.py up`), and unplug the
key.

### After any restore

- Unplug the key; make sure no copy of it is left on the host.
- Run the job once (`python scripts\backup\config_secrets_backup.py run --age
  C:\Tools\age\age.exe`) so the newest archive holds the restored state, and
  `check-backup-coverage.ps1` reports CLEAN.

## When it fails

| Symptom | Meaning | Fix |
|---|---|---|
| `REFUSED: NO AGE RECIPIENT CONFIGURED` | no `secrets/config-backup/age-recipients.txt` | setup step 3 |
| `REFUSED: ... starts with a byte-order mark` | the recipients file was written with a BOM | rewrite it with `Set-Content -Encoding ascii` (setup step 3) |
| `archive FAILED: age ... malformed recipient` | age rejected the recipient line (a typo in the `age1...` key) | copy the public key again from `age-keygen -y <key file>` |
| `[ERROR] LINK <path>` | a symlink/junction where a secret would be archived | replace it with the real file, or `[[exclude]]` it with a reason |
| `[ERROR] pin-ignores FAILED` | `.git/info/exclude` could not be rewritten (e.g. not UTF-8) | fix the file by hand; the archive itself was written |
| `REFUSED: ... holds an age PRIVATE key` | the private key is on the host | move it offline, delete it, put the public key there |
| `REFUSED: ... is not a pinned age build` | `--age` points at another binary | setup step 1, or pin the new build in a commit |
| `[ERROR] MISSING <file>` | a required or bind-mounted file is absent, or a directory Docker made in its place | "Restore one file" |
| `[ERROR] RENDER <plane>` | `docker compose config` failed for that plane | run the printed command by hand; its bind mounts are not checked until it renders |
| `[ERROR] OVERSIZE <file>` | a file over 5 MiB was found | it is data: `[[exclude]]` it with a reason in the policy |
| `[ERROR] OUTSIDE <path>` | a compose file bind-mounts a credential from outside the repo | move it under the repo, or `[[outside]]` with who backs it up |
| coverage `UNCOVERED <file>` | a new file appeared since the last run | the next nightly run covers it, or run the job now |

Adding a file the derivation cannot find: an `[[include]]` (or `[[required]]` if the
stack cannot start without it) in `scripts/backup/config-secrets.toml`.
