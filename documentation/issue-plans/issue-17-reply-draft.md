# Issue #17 — draft public reply (host/operator lane)

NOT part of any worker payload. Posting requires operator approval in the
MM thread; post on close, referencing the merged fix commit.

### DRAFT public reply (NOT posted -- requires operator approval in the MM thread first)

> Thanks for the detailed write-up, and especially for including the exact
> rendered line -- that made this quick to pin down.
>
> Your analysis is correct. In `_extract_kb_name`, the alternation
> `(?:into|to|as|kb:|knowledge[- ]?base[: ])` has no word boundaries, so on a
> tail like `</query> <mandatory>` the leftmost match is the `to` inside
> `manda[to]ry>`, and the lazy end-anchored group then captures `ry>`.
> `_normalize_kb_name` doesn't catch it either: it strips only complete
> `<...>` tags, and `ry>` has no opening `<`. So the domain fallback
> (`SmolCrawl - docks.gaggimate.eu`) is never reached. That is precisely the
> output you saw.
>
> The reason we're closing this rather than patching it: the
> `SmolCrawl Knowledge Builder` pipeline was retired on 2026-08-21 in commit
> `a18aa0c`. Its only purpose was crawl -> Open WebUI Knowledge upload, and we
> retired the Open WebUI Knowledge layer the day before in `9223516`; the
> pipeline should have gone with it. The `smolcrawl-pipelines` container and
> its compose blocks are gone (only an orphaned `smolcrawl-data` volume comment
> remains), and no container by that name exists on the host. We kept the
> `smolcrawl/` crawler library in-tree because it may serve a future ingestion
> path, but the Open WebUI pipe file that contains this function is no longer
> built or run. Based on our pre-removal check -- zero log activity in the 14
> days before retirement -- your observation dates from before that.
>
> So the bug is real and your diagnosis is right; there isn't a running code
> path left for it to affect today, but we've decided to take your fix into the
> retained source anyway so any future revival is correct from day one:
> word-bounded `into|to|as` plus a capture class that excludes tag delimiters,
> with tests locking in your exact repro. This issue will close with the fix
> commit referenced. Two notes for that day, in case they're
> useful to you: excluding `'` from the class as written would also break a
> legitimate name like `into Bill's Docs`, so `[^<>\n\r]` is the safer class;
> and while tracing this we found that the second pattern (the
> `with|using|from` terminator) is dead code today, since the `$`-anchored
> pattern is tried first and always matches -- so `into My KB with images`
> currently keeps the whole tail.
>
> Thanks again for taking the time to trace it to the regex.
```

