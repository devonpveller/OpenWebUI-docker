# Search-engine alternatives for the Private Search Gateway (2026-09-11)

**Slot:** `PLAN-research-trust-2026-09-11.md` Phase 1.3 ("Engine health, measured and surfaced").
**Evidence base:** `documentation/notes/research-audit-optiplex-100hz-2026-09-11.md`.
**Scope:** options analysis for the operator. **Nothing live was changed.** No `settings.yml` edit, no
container restart, no compose action, no git state touched. Two throwaway containers
(`--label ai-stack.harness.owner=search-alt`) were created on `search_search-net` and removed (§7).

**Headline:** the premise "Bing behind Mullvad collapses queries" reproduces exactly, but the two
proposed explanations are both wrong, and the cheapest fix is not a new engine.

- It is **not the VPN exit IP**: on the *same* exit IP, in the *same* minute, Google/Brave/Qwant return
  on-topic results while Bing returns "Dell". (§1.3)
- It is **not fixed by the upstream bing patch**: SearXNG PR #6671 ("bing first word results") merged
  2026-09-11; on an image containing it, Bing still scores **0.00** on all 12 queries. (§1.4)
- **Yandex and Seznam have been working the whole time and are switched off in our own
  `settings.yml`.** Yandex scores median overlap **0.57** on the exact image we are running today. The
  audit's "Bing is effectively the only general engine" is true only because we disabled the others
  on 2026-06-14 and never re-measured. (§2.1)

---

## 0. Method

**Term-overlap ratio** (the number used throughout): fraction of a hit set whose `title + content`
contains at least **2 distinct non-stopword query terms**, word-boundary matched, case-folded.
0.00 = not one hit in ten mentioned two words of the query.

**Query set:** the 12 `needs` of the two audited jobs, verbatim:

```
docker exec openbrain-db psql -U postgres -d openbrain -Atc \
  "select result->'needs' from research_jobs where id in
   ('ce398d06-32cd-47ad-92f0-a12cbf5ed114','8c9b4f1d-f9a8-43a8-8d18-3af46f93bbe9')"
```

**Live versions** (`docker inspect searxng`, 2026-09-11):

| | value |
|---|---|
| live image | `searxng/searxng:latest` = `sha256:193604d4...`, label `org.opencontainers.image.version` = **`2026.5.17-d7e8b7cd1`**, built **2026-05-17T16:44:30Z** - **117 days old** |
| upstream latest | `2026.9.11-61d660276`, pushed 2026-09-11 (`https://hub.docker.com/v2/repositories/searxng/searxng/tags`) |
| exit IP | `23.159.216.147`, **United States / California / Orange** (`docker logs search-vpn`, `[ip getter]` line, 2026-09-07T23:48:30Z) |
| relay setting | `.env:157 MULLVAD_COUNTRIES=USA` (compose default is `Netherlands`) |

**Test rigs.** Because engine-suspension state is per-instance, measurement ran on throwaways, not on
live. `searxng-alt` runs the **byte-identical image and revision as live** (`2026.5.17+d7e8b7cd1`) with
the same `proxies: all:// -> http://vpn:8888`; `searxng-new` is the same settings on `2026.9.11-61d660276`.
Live was spot-checked to confirm the rig matches it (§1.2).

**`engines=` activates disabled engines.** Contrary to the brief's assumption, SearXNG's `engines=`
query parameter selects from *all configured* engines, not only enabled ones - so most of this could
be measured without editing `settings.yml` at all. Verified: `engines=brave` on **live** returned 20
results while `brave` is `disabled: true` in `search-gateway/searxng/settings.yml`.
*Caveat:* if the name is not a configured engine, SearXNG silently **falls back to the full default
set** instead of erroring (seen with `engines=startpage` on the new image, which returned 77 results
from bing/brave/google/qwant/yandex). Any per-engine probe must verify attribution via `result.engines`.

---

## 1. Reproduction of the collapse

### 1.1 Live gateway, `127.0.0.1:8085` - reproduces

```
curl -s -G "http://127.0.0.1:8085/search" \
  --data-urlencode "q=Dell OptiPlex 3050 common hardware failure modes capacitor CPU socket defects" \
  --data-urlencode "format=json"
```
-> `number_of_results: 10`, engine `bing`, hits 1-3:
`Computers, Monitors & Technology Solutions | Dell USA` /
`Support Home | Dell US` / `Dell - Wikipedia`. **Overlap 0.00.**

### 1.2 Live spot-checks (short queries, so length is not the cause)

| query (live `:8085`) | n | first hit |
|---|---|---|
| `OptiPlex 3050 capacitor failure` | 10 | `Dell OptiPlex - Wikipedia` |
| `100 Hz tone motion sickness study` | 10 | `The 100 (TV series) - Wikipedia` |

Identical output from `searxng-alt` -> the rig is a faithful stand-in for live.

### 1.3 The decisive control: one image versus the other, same exit, same minute

Both containers restarted to clear suspension state, then queried within ~60 s of each other with
`q=Dell OptiPlex 3050 common hardware failure modes capacitor CPU socket defects`:

| engine | `2026.5.17` (**= live**) | `2026.9.11` |
|---|---|---|
| **bing** | 10 hits - `Dell USA` / `Support Home` / `Dell - Wikipedia` | 10 hits - **byte-identical junk** |
| **google** | **0 hits, no error reported** | 10 hits - `A Reference Guide to the Dell OptiPlex Diagnostic Indicators`, `OptiPlex 3050 Tower Owner's Manual - Dell` |
| **qwant** | `access denied` | 10 hits - `Support for OptiPlex 3050 Tower \| Diagnostics \| Dell US` |
| **brave** | `too many requests` | 20 hits - `OptiPlex 3050 Small Form Factor Owner's Manual \| Dell US` |
| **duckduckgo** | `CAPTCHA` | `CAPTCHA` |
| **mojeek** | `access denied` | `access denied` |

**This kills the "it's the VPN IP" hypothesis.** Same IP, same proxy, same second: Google, Qwant and
Brave all return on-topic pages. What changed is our SearXNG.

**Mechanism (verified in the images, not inferred):**

```
docker exec searxng-alt sh -c "ls /usr/local/searxng/.venv/lib/python3.14/site-packages/ | grep -iE 'curl_cffi|httpx'"
  -> httpx / httpx-0.28.1.dist-info / httpx_socks
docker exec searxng-new sh -c "... same ..."
  -> curl_cffi / curl_cffi-0.16.1.dist-info
```

Upstream commit `be836e6` **2026-09-04** `[mod] network: migrate to curl_cffi` replaced `httpx` with
`curl_cffi` (browser TLS/JA3 impersonation). Our live log carries the corresponding symptom:
`ERROR:searx.engines.google: requests exception ... [SSL: TLSV1_ALERT_PROTOCOL_VERSION] tlsv1 alert
protocol version (_ssl.c:1081)` - Google rejecting the `httpx` TLS handshake outright. That we are
being fingerprinted at the TLS layer is **verified**; that `curl_cffi` is the *sole* reason
google/qwant/brave recover is **plausible but unproven** (the images differ by ~4 months of commits).

### 1.4 The upstream bing fix does NOT fix our bing

SearXNG PR **#6671** "[fix] engines: bing first word results" (commit `ffe96f8`), opened 2026-09-07,
**merged 2026-09-11**, closing issue **#4964** ("bing: results are often unrelevant to the search
query", opened 2025-07-02). Source: `https://github.com/searxng/searxng/pull/6671`.

I verified the patch is present in the image I tested:

```
docker exec searxng-alt grep -n 'override_accept_language' .../searx/engines/bing.py
  -> 75: def override_accept_language(...)   102: override_accept_language(params, engine_region)
docker exec searxng-new grep -nE 'setlang|cn.*ru' .../searx/engines/bing.py
  -> 87: query_params["setlang"] = lang
  -> 88: if cc and cc not in ("us", "cn", "ru"):  # bing just sends junk for these
```

**And bing still scores 0.00 on all 12 queries on the patched image** (§2.2). The patch's own comment
says Bing sends junk for `cc` in `us/cn/ru`; our exit *is* `us`, and the workaround is to omit `cc`,
which evidently is not enough from a US datacenter range. **Do not expect the image upgrade to fix
Bing.** Expect it to make Bing irrelevant.

---

## 2. Keyless engines - measured

All numbers: 12 queries, one request per query with all engines selected, per-engine attribution via
`result.engines`. "answered" = queries where the engine returned at least 1 hit.

### 2.1 On the image we are running today (`2026.5.17`, identical to live)

| engine | answered | median hits | **median overlap** | max overlap | failure reported |
|---|---|---|---|---|---|
| **yandex** | 12/12 | 15 | **0.57** | 1.00 | - |
| **seznam** | 12/12 | 10 | **0.15** | 0.60 | - |
| **bing** | 12/12 | 10 | **0.00** | 0.00 | - (returns junk *silently*) |
| searchmysite | 2/12 | 1.5 | 0.00 | 0.00 | - |
| google | 0/12 | 0 | - | - | 0 results, **no error raised** |
| brave | 0/12 | - | - | - | `too many requests` |
| duckduckgo | 0/12 | - | - | - | `CAPTCHA` |
| startpage | 0/12 | - | - | - | `CAPTCHA` |
| qwant | 0/12 | - | - | - | `access denied` |
| mojeek | 0/12 | - | - | - | `access denied` (403) |
| yahoo | 0/12 | - | - | - | `parsing error` / `HTTP protocol error` |
| yep | 0/12 | - | - | - | `HTTP error` |
| mwmbl / presearch | 0/12 | - | - | - | `timeout` |
| yacy | 0/12 | - | - | - | `HTTP connection error` |
| wiby / crowdview / encyclosearch | 0/12 | - | - | - | no hits, no error |

**The operationally important row is `yandex 0.57` with `disabled: true` in our settings.yml.** On the
image we are running, on the exit we are using, today.

### 2.2 On upstream latest (`2026.9.11-61d660276`), same settings, same exit

| engine | answered | median hits | **median overlap** | max | note |
|---|---|---|---|---|---|
| **brave** | 7/12 | 20 | **0.95** | 1.00 | rate-limited (`too many requests`) after ~7 queries in a burst |
| **qwant** | 10/12 | 10 | **0.90** | 1.00 | 2 CAPTCHAs |
| **google** | 12/12 | 10 | **0.90** | 1.00 | **no CAPTCHA in 12 queries** |
| **yandex** | 12/12 | 15 | **0.60** | 1.00 | - |
| seznam | 10/12 | 10 | 0.15 | 0.60 | 2 timeouts |
| **bing** | 12/12 | 10 | **0.00** | 0.00 | unchanged by PR #6671 |
| duckduckgo | 0/12 | - | - | - | `CAPTCHA` |
| mojeek | 0/12 | - | - | - | `access denied` (403) |
| yahoo | 0/12 | - | - | - | `parsing error` |
| yacy | 0/12 | - | - | - | `SSL error: certificate validation has failed` |
| startpage, presearch | n/a | - | - | - | **engines removed upstream** between 2026-05-17 and 2026-09-11 |

### 2.3 Engines that only exist on the newer image (4-query subset: needs #1, #8, #4, #12)

| engine | answered | median hits | median overlap | what it is |
|---|---|---|---|---|
| **duckduckgo web** | 4/4 | 9 | **1.00** | new DDG engine; the old `duckduckgo` still CAPTCHAs |
| **resulthunter** | 4/4 | 19 | **1.00** | metasearch front-end - **operator unverified** |
| **tusksearch** | 4/4 | 15 | **1.00** | metasearch front-end - **operator unverified** |
| **zapmeta** | 4/4 | 9 | **1.00** | metasearch front-end - **operator unverified** |
| **vuhuv** | 4/4 | 10 | **0.95** | metasearch front-end - **operator unverified** |
| **privacywall** | 4/4 | 10 | **0.85** | metasearch front-end - **operator unverified** |
| **google cse** | 4/4 | 20 | **0.80** | `engine: google_cse` with **no `api_key`/`cx` configured** and it still answered |
| naver | 4/4 | 11.5 | 0.33 | Korean |
| 360search | 4/4 | 6.5 | 0.29 | Chinese |
| quark | 4/4 | 9.5 | 0.00 | Chinese; own collapse |
| baidu / fastbot / fireball / sogou | 0/4 | - | - | CAPTCHA / access denied / crash |

Engine-count diff: **244 -> 267** configured engines. Added include `duckduckgo web`, `privacywall`,
`zapmeta`, `resulthunter`, `tusksearch`, `vuhuv`, `fireball`, `searchch`, `google cse`, `fastbot`,
`ayo`. Removed: `startpage`, `presearch`, `aol`, `reddit`, `deviantart`, `podcastindex`, `cara`, `svgrepo`.

**Caveat the operator must weigh:** the five 1.00-scoring newcomers are third-party metasearch
proxies. I did **not** verify who runs them, what they log, or whether they are ad-affiliate SERP
resellers. They score best on relevance and worst on provenance. `duckduckgo web` and `google cse`
are the two newcomers with a known operator.

### 2.4 Could not be tested, and why

| engine | why not |
|---|---|
| `startpage`, `presearch` | **removed from upstream** on the new image; measurable only on the old one, where both fail (`CAPTCHA` / `timeout`) |
| `marginalia` | SearXNG's `marginalia.py` requires an `api_key` setting - keyed, not keyless. Free non-commercial key by email. Untested. |
| Anything from the operator's own IP | I have no path to a non-VPN egress. **Whether these engines behave differently from a residential IP is untested.** |
| `brave` sustained throughput | rate-limited after ~7 queries in a burst; whether it survives a 6-20 search job at production pacing is **untested** |

---

## 3. Keyed API options

Researched externally; **URLs given, all read 2026-09-11**. No account or key was created.

### 3.1 Two incumbents are gone - this is the load-bearing part

| API | Status | Source |
|---|---|---|
| **Bing Web Search API** | **RETIRED 2025-08-11.** "Bing Search APIs will be retired on August 11, 2025 ... decommissioned completely". Replacement is *Grounding with Bing Search* inside Azure AI Agents - an agent tool, **not** a SERP API returning `results[]`. | `https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement` |
| **Google Custom Search JSON API** | **Closed to new customers; discontinued 2027-01-01.** "The Custom Search JSON API is closed to new customers ... existing customers have until January 1, 2027 to transition." The $5/1k price is real but **unobtainable - we cannot sign up.** | `https://developers.google.com/custom-search/v1/overview` |

Any plan that names Google PSE as the fallback is planning against a dead service.

### 3.2 Live options

Volume assumption: 15-25 jobs/day x 6-20 searches = **~100-500 queries/day, ~3k-15k/month**.

| API | Free tier | Price /1k | ~10k/mo | Integration path | Long-NL query quality | Privacy cost |
|---|---|---|---|---|---|---|
| **Brave Search** | $5 credit/mo (~1k queries) | **$5.00**, 50 QPS | ~$45 | SearXNG ships **`braveapi.py`** -> `settings.yml` block only | keyless Brave measured **0.95** here; API is the same index | key = billing identity; **Zero Data Retention is Enterprise-only**; standard-plan retention unverified |
| **Mojeek** | trial on request | **GBP 2 CPM** | **~GBP 20** (cheapest verified) | gateway provider (stub exists) or SearXNG `mojeek` w/ key | untested | metered per key |
| **Serper.dev** | 2,500 one-time | **$1.00 - UNVERIFIED** (serper.dev/pricing is 404; third-party only) | ~$10 | new gateway provider | untested | key = identity |
| **Tavily** | 1,000 credits/mo | $6.70-8.00 (advanced search = **2 credits**) | ~$67-80 | gateway **already speaks Tavily** (`/tavily/search` shim) - but that is our *server* face, not a client | built for this contract | key = identity |
| **Exa** | $10/mo credit | $7.00 + $1/1k contents | ~$70-80 | SearXNG ships `exaapi.py` | untested | key = identity |
| **You.com** | **100 q/day (~3,000/mo)** - best free tier found | $5.00 | ~$50, or **$0 at <=100/day** | new gateway provider | untested | key = identity |
| **Kagi** | none | **$12.00** | ~$120 | SearXNG ships `kagi.py`; gateway stub exists | untested | key = identity |
| **SerpApi** | 250/mo | $10-15 | $150 tier | new gateway provider | untested | "ZeroTrace" is a paid-plan feature |
| **Marginalia** | **free**, shared `public` key | free (non-commercial) | **$0** | SearXNG `marginalia.py` (`api_key` required) | small independent index - supplement only | public key is *shared* across all users -> least attributable |

Sources: `https://brave.com/search/api/` / `https://www.mojeek.com/services/search/web-search-api/` /
`https://serper.dev/` / `https://tavily.com/pricing` + `https://docs.tavily.com/documentation/api-credits` /
`https://exa.ai/pricing` / `https://you.com/platform/upgrade` / `https://kagi.com/api/pricing` /
`https://serpapi.com/pricing` / `https://about.marginalia-search.com/article/api/`

### 3.3 Why mojeek 403s - answered, and it is not the IP

Two stated policies, read 2026-09-11:

- `https://www.mojeek.com/robots.txt` - `User-agent: *` / **`Disallow: /search`** - the exact path
  SearXNG's keyless `mojeek` engine requests.
- `https://www.mojeek.com/about/terms.html` - you may not *"use or access Our Services by any
  automated means **(unless you are an authorised Mojeek API user)**"*.

**The keyless mojeek engine is scraping a path Mojeek disallows.** The 403 is policy enforcement, not
a transient block; rotating the relay will not fix it, and `disabled: false` for `mojeek` in our
settings.yml has us knocking on a door that is contractually shut. Live log: 47 x
`ERROR:searx.engines.mojeek: HTTP error 403` in the last 7 days. The only legitimate route is the
paid API (~GBP 2 CPM).

### 3.4 The privacy trade-off, stated plainly

`search-gateway/README.md` "Privacy enforcement" makes the guarantee **network-layer**: `search-net`
is `internal: true`, so SearXNG *physically cannot* reach the internet except through the kill-switched
Mullvad proxy. Queries are untraceable to the operator by construction.

**An API key destroys that property and Mullvad cannot restore it.** Every keyed option above meters
per key, so the vendor holds a durable, billing-identity-linked log of every research query the stack
ever issues - Mullvad hides the IP, the key names the customer. The question is not "will they see my
IP" but "am I willing for one vendor to hold the full query stream of my research engine".

The only options that preserve the current property are **keyless engines** and **Marginalia's shared
public key**. This is the operator's call and it is a values call, not a technical one.

---

## 4. Query-shape mitigation - measured, and it does not work

Bing on `2026.5.17` (= live), 15 variants. `long1` = the audit's OptiPlex query, `long2` = the 100 Hz
clinical-trials need.

| # | variant | n | overlap | first hit |
|---|---|---|---|---|
| A | long1 baseline | 10 | **0.00** | `Computers, Monitors & Technology Solutions \| Dell USA` |
| B | long1 `language=en-US` | 10 | **0.00** | *identical* |
| C | long1 `language=all` | 10 | **0.00** | *identical* |
| D | long1 `safesearch=0` | 10 | **0.00** | *identical* |
| E | long1 `safesearch=1` | 10 | **0.00** | *identical* |
| F | long1 `time_range=year` | **0** | - | - |
| G | long2 baseline | 10 | **0.00** | `SPECIFIC (Cambridge Dictionary, zh-Hans)` |
| H | long2 `language=en-US` | 10 | **0.00** | *identical* |
| I | kw `OptiPlex 3050 capacitor failure` | 10 | **0.00** | `Dell OptiPlex - Wikipedia` |
| J | kw `OptiPlex 3050 problems` | 10 | **0.00** | `Dell OptiPlex - Wikipedia` |
| K | kw `Dell OptiPlex 3050 motherboard repair` | 10 | **0.00** | `Dell USA` |
| L | kw `100 Hz tone motion sickness study` | 10 | **0.00** | `The 100 (TV series) - Wikipedia` |
| M | kw `100 Hz auditory motion sickness trial` | 10 | **0.00** | `The 100 (TV series) - Wikipedia` |
| N | 3 tokens `OptiPlex 3050 failure` | 10 | **0.00** | `Dell OptiPlex - Wikipedia` |
| O | **2 tokens** `OptiPlex capacitor` | 10 | **0.00** | `Dell OptiPlex - Wikipedia` |

**15/15 at 0.00.** A two-token query collapses. **This refutes plan item D7 as a Bing remedy**:
"Round-1 queries are the DECOMPOSE questions verbatim (long natural language) -> start with keyword
queries" is a good idea for other reasons, but it does nothing for this failure, because the failure is
first-token-only, not length-sensitive. `time_range` is worse than useless (0 results).

The collapse token is always the **first non-trivial token** (`Dell`, `OptiPlex`, `100`, `specific`),
consistent with upstream issue #4964.

---

## 5. Mullvad relay

| fact | evidence |
|---|---|
| current exit | `23.159.216.147`, **United States / California / Orange** - `docker logs search-vpn`, `[ip getter]` line, 2026-09-07T23:48:30Z |
| WG endpoint | `23.159.216.127:51820` - same log, `[wireguard] Connecting to ...` |
| relay selection is exposed | **`.env:157 MULLVAD_COUNTRIES=USA`** -> `search/docker-compose.yml` `SERVER_COUNTRIES=${MULLVAD_COUNTRIES:-Netherlands}`. gluetun also accepts `SERVER_CITIES`, `SERVER_HOSTNAMES`, `SERVER_NUMBER` (all present and empty - `docker inspect search-vpn`) but the compose file wires **only** `SERVER_COUNTRIES`. Rotating = edit `.env`, recreate `vpn` (search-plane lease). Not done here. |
| does the relay cause the collapse? | **No.** §1.3 is the control: same relay, Google/Brave/Qwant fine, Bing junk. |
| would a non-US relay help Bing? | **Untested.** Worth one experiment: SearXNG's own bing fix comments that `cc` in `us/cn/ru` "just sends junk", and our exit is `us`. Cheap to try (`MULLVAD_COUNTRIES=Netherlands`, the compose default), but it is a guess, and §1.3 shows the *engine mix* is the bigger lever. |

**Which engines block this range** - live `docker logs searxng`, 2026-09-04 -> 2026-09-11 (7 days):

| engine | log lines | reason |
|---|---|---|
| mojeek | **47** | `HTTP error 403` (ToS, §3.3 - not an IP problem) |
| wikipedia | **21** | `Too many request` (429) x10, `HTTP error 403` x10, plus one 400 from a **malformed query** - SearXNG sent a whole sentence as a Wikipedia page title (`/page/summary/The%20article%20does%20not%20disclose%20pricing...`) |
| google | 3 | `CAPTCHA (suspended_time=3600)` - only **2026-09-11 05:52 and 06:57**; plus `[SSL: TLSV1_ALERT_PROTOCOL_VERSION]` and a timeout |
| startpage / qwant / duckduckgo | 1 each | `CAPTCHA` / `Access denied` - they are disabled, so these are stray |
| bing | 1 | one timeout. **Bing never errors - it returns 10 junk hits with HTTP 200.** |

That last row is the real operational hazard: **the failing engine is the one that never reports a
failure.** Every alerting idea that keys off suspension logs will show Bing green forever.

---

## 6. Ranked shortlist

### Option 1 - Bump the SearXNG image and re-enable the engines that work. No key, no money, no identity exposure.

Measured effect: median overlap on the audited queries goes from **bing 0.00 / yandex disabled** to
**google 0.90, brave 0.95, qwant 0.90, yandex 0.60, duckduckgo web 1.00** - with 3-4 engines answering
instead of 1.

| change | file |
|---|---|
| pin `SEARXNG_IMAGE=searxng/searxng:2026.9.11-61d660276` (do **not** leave `:latest` floating - `watchtower.enable=false` is set precisely so privacy infra is not silently swapped) | `.env` (new var; `search/docker-compose.yml` already reads `${SEARXNG_IMAGE:-...}`) |
| `duckduckgo: disabled: false` (the *new* `duckduckgo web` engine), `brave: false`, `qwant: false`, `yandex: false`; `bing: true`; `mojeek: true` (§3.3 - we are violating their ToS); drop the now-nonexistent `startpage`/`presearch` entries; rewrite the stale 2026-06-14 comment block | `search-gateway/searxng/settings.yml` |
| none | `OB1/integrations/research-service/index.ts` - `searchWeb()` contract is untouched |
| none | gateway providers |

**Cost: zero. Risk: a 4-month image jump on privacy infrastructure.** That is a real risk and the
reason `watchtower.enable=false` exists - it needs the search-plane lease, a settings review against
the new `settings.template.yml`, and a re-verification that `routes/searxng_compat.py` still matches
(the README already mandates this on OWUI bumps; same logic applies here).
**Operator decides:** accept the version jump. Nothing else.

**Caveat measured, not assumed:** brave rate-limited after ~7 queries in a burst and qwant CAPTCHA'd
on 2 of 12. Multi-engine gives redundancy, not immunity. This option needs the plan's Phase 1.1
collapse detector *anyway* - see below.

### Option 2 - Option 1 **plus** the collapse detector, treating engine relevance as a health signal.

Option 1 alone is one upstream commit away from silently regressing, and §5 shows the log will not
tell us. The plan's Phase 1.1 `classifyHits()` is the right mechanism and this analysis supplies its
threshold empirically: **working engines score 0.57-1.00; the broken one scores 0.00.** A cut at
**0.3** separates them with no overlap across 12 queries x 7 engines.

| change | file |
|---|---|
| `classifyHits(query, hits)` + `fetchStats.search` | `OB1/integrations/research-service/` (new `search-quality.ts`, wired into `index.ts` `searchWeb()`) |
| per-engine overlap in `/health` | `search-gateway/gateway/src/gateway/` (the payload already passes through `normalize_searxng_payload`) |
| `search: N engines answering` | `scripts/stack/stack.ps1 health` |

**Cost: zero money, one work item.** This is Phase 1.1 + 1.3 of the existing plan, unchanged - this
analysis just supplies the numbers. **Operator decides:** nothing new.

### Option 3 - Add **one** keyed API as a gateway provider, behind the keyless tier.

Only worth doing if Options 1-2 prove insufficient under production pacing (untested). If taken:

- **Brave Search API, $5/1k, ~$45/mo at 10k** - best fit. SearXNG already ships `braveapi.py`, so it is
  a `settings.yml` block with `api_key`, not new gateway code; and the keyless Brave engine already
  measured **0.95** here, so the index is known-good for these queries.
- **You.com, 100 q/day free** - covers ~3,000/mo at **$0**. Needs a new gateway provider
  (`providers/`, `PROVIDER_PRIORITY`, Redis quota bucket per the README roadmap).
- **Mojeek, ~GBP 20/mo** - cheapest, and it is also the only way to use Mojeek legally at all (§3.3).

**Operator decides, and these are the only real decisions in this document:**
1. **Money:** $0-45/mo.
2. **Identity:** a key ties the stack's entire query stream to a billing identity, permanently, and
   Mullvad does not mitigate it. This contradicts the gateway's stated reason to exist.
3. **Where the key lives:** `.env` + a named env var on `gateway` or `searxng` - note
   `search/docker-compose.yml` deliberately has **no `env_file`** and is guarded by
   `scripts/checks/check-env-file-scope.ps1`, so the variable must be named explicitly.

**Not recommended without a decision on (2).** Options 1-2 cost nothing and are measured to fix the
audited failure; Option 3 should be judged on whether they hold up in production, not adopted pre-emptively.

**Explicitly rejected:** Google PSE (closed to new customers, dies 2027-01-01), Bing Web Search API
(retired 2025-08-11), keyless Mojeek (ToS violation), keyless Startpage/Presearch (removed upstream).

---

## 7. Cleanup

| artifact | disposition |
|---|---|
| `searxng-alt`, `searxng-new` (label `ai-stack.harness.owner=search-alt`, network `search_search-net`) | **removed** - see §8 command |
| scratchpad `searxng-alt/settings.yml`, `measure.py`, `shape.py`, `*.json` | session scratchpad only, never in the repo |
| image `searxng/searxng:2026.9.11-61d660276` | **pulled and left in the local cache.** Deliberate: it is a *new tag*, so `searxng/searxng:latest` - which `search/docker-compose.yml` resolves - was **not** modified, and the live container was not touched. It is pre-staged for Option 1. Remove with `docker image rm searxng/searxng:2026.9.11-61d660276` if Option 1 is rejected. |
| live `settings.yml` / containers / compose / git | **untouched** |

## 8. What I could not verify

1. **Behaviour from the operator's own IP.** No non-VPN egress available. Whether Bing collapses from
   a residential IP is **untested** - §1.3 makes it moot for the decision, but it is not disproved.
2. **Whether `curl_cffi` alone explains the google/qwant/brave recovery.** The images differ by ~4
   months. The TLS-rejection log line is verified; sole causation is **inferred**.
3. **Whether a non-US Mullvad relay changes Bing.** Not tested; changing the relay was out of scope.
4. **Who operates `resulthunter`, `tusksearch`, `zapmeta`, `vuhuv`, `privacywall`** and what they log.
   They score 0.85-1.00 and should not be enabled on a privacy-motivated stack without that answer.
5. **Why `google cse` answers with no `api_key`/`cx` configured.** Observed, not explained. If it is
   scraping an endpoint that expects a key, it may be as legally shaky as keyless mojeek.
6. **Serper's paid price.** `serper.dev/pricing` returns 404; the $1/1k figure is third-party only.
   Reported gotchas (credits expire after 6 months; 11-100 results costs 2 credits) are also third-party.
7. **Standard-plan query retention** for Brave, Tavily, Exa, You.com, Kagi, Serper, SerpApi. Per-key
   *attribution* is structural for all of them; retention *duration* is unverified.
8. **Sustained throughput.** Brave rate-limited after ~7 burst queries; behaviour under a real
   6-20-search job at production pacing is untested.
9. **Marginalia** - never measured (needs a key even for the free tier).
10. **`engines=` fallback blast radius.** Verified for `startpage`/`presearch` on the new image. Whether
    the live gateway can be made to fall back this way by a malformed `engines=` is untested - worth a
    look, since it would mean a typo silently queries every configured engine.

### Verified reproduction commands

```bash
# 1. live collapse (no lease needed, read-only GET)
curl -s -G "http://127.0.0.1:8085/search" --data-urlencode "q=OptiPlex 3050 capacitor failure" \
     --data-urlencode "format=json"

# 2. a disabled engine, on LIVE, without editing settings.yml
docker exec searxng python -c "import json,urllib.request,urllib.parse; \
print(len(json.load(urllib.request.urlopen('http://localhost:8080/search?'+ \
urllib.parse.urlencode({'q':'Dell OptiPlex 3050 hardware failure','format':'json','engines':'yandex'})))['results']))"

# 3. throwaway rig (what this analysis used) and its removal
docker run -d --name searxng-new --label ai-stack.harness.owner=search-alt \
  --network search_search-net -e SEARXNG_SECRET=<throwaway> \
  -v <scratchpad>/searxng-alt:/etc/searxng:ro searxng/searxng:2026.9.11-61d660276
docker rm -f searxng-alt searxng-new
```

### Overlap-ratio scorer (the measurement, reproducible)

```python
STOP = set("a an the and or of for to in on at by with from as is are was were be been being "
           "what which who whom how why when where does do did can could should would will shall "
           "this that these those it its their there here about into over under than then so such "
           "not no have has had specific most more established using used use other others reported "
           "known associated required steps run interpret check assess indicate prior compare affect".split())

def toks(s):
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 1]

def overlap(query, hits):
    qt = list(dict.fromkeys(toks(query)))
    good = 0
    for h in hits:
        text = ((h.get('title') or '') + ' ' + (h.get('content') or '')).lower()
        present = {t for t in qt
                   if re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', text)}
        if len(present) >= 2:
            good += 1
    return good / len(hits) if hits else 0.0
```
