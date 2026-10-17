# Route the StreamEast listing fetch through Playwright to clear Cloudflare

The on-demand resolver stopped returning streams because `_find_match_url` fetched the NFL listing with `requests`, and `https://v2.streameast.ga/nfl-streams/` now answers that fetch with a Cloudflare "managed challenge" (HTTP 403) instead of the listing. With no `/nfl/<a>-vs-<b>/` anchors to match, `_find_match_url` returned `None`, `resolve_match` returned `None`, and the UI showed "Stream no disponible aún" on tap — even though the ESPN agenda cards (a different API) still showed live scores. The fix downloads the listing with the headless Chromium the module already uses for stream capture, reusing the same DoH + UA launch pattern, and keeps the `requests` path as a fallback for networks without the challenge. The match-page parsing (regex, slug-term matching) is unchanged; it was extracted verbatim into a pure `_pick_match_url` so it can be asserted offline against a saved fixture of today's real markup.

Watch for:
- (confirmed) The diagnosis correctly rejects the user's "format changed" hypothesis: the markup is unchanged, the fetch is what broke. This matches the actual diff — no regex or selector was touched.
- (confirmed) Two Chromium launches per tap (listing + capture) rather than one shared session. Deliberate and documented; acceptable for a user-initiated action but worth knowing.
- (likely) The parallel Cloudflare 403 in `_resolve_hybrid` (`resolvers.py`) is left unfixed by design, flagged in the commit body for a separate task.
- (confirmed) End-to-end `.m3u8` capture was not run anywhere (Playwright absent in the dev env); only the diagnosis, the parse logic, and an offline fixture test were verified. Final confirmation is deferred to the Pi.

**Verdict**: APPROVED

## High-level view

The root-cause diagnosis is sound and backed by live evidence recorded in `verification.md`: the same URL returns a 403 Cloudflare challenge body (5485 bytes, no `-vs-` links) under `requests`, and the identical parse logic yields the correct match URL when fed browser-rendered markup. The failure is in transport, not parsing, so the regex `_MATCH_PATTERN` and the slug-term matching are left untouched — exactly the minimal change the plan called for.

`_fetch_listing_html` reuses `_import_playwright`, `_chromium_host_rules(DEFAULT_DOH_HOSTS)`, and `_DEFAULT_UA` — the same launch recipe already present in `resolve_match`, `resolvers.py`, and `deporte_libre.py` — so the two steps share one approach rather than inventing a new one, and the Spanish-commented style is preserved.

`config/sources.yaml` was correctly left alone. The on-demand path uses the hardcoded constants in `ondemand.py`, and touching the YAML would have reached into the unrelated hybrid path. Patterns stay consistent because nothing about the pattern changed.

The resilience approach is proportionate: browser first, `requests` fallback second, `None` on failure. It is no worse than today on any network and adds no hard dependency. The one real gap is that the full user-visible path (tap → `.m3u8` playing) was never exercised, because Playwright is not installed in the dev environment; that verification is explicitly deferred to the device.

<details>
<summary>Issues (3)</summary>

1. **End-to-end capture unverified** — the `.m3u8` capture step was never run (Playwright absent in dev); the Pi-side live check in `verification.md` step 5 is still pending and is the only thing that proves the user-visible tap path works. Non-blocking but must be run on the device before declaring the user's symptom fixed.
2. **Hybrid path still broken** — `_resolve_hybrid` in `resolvers.py` has the same Cloudflare 403 under `requests`; correctly out of scope here, flagged for a separate task. Track it so the hourly pre-scrape doesn't silently keep failing.
3. **Weak decoy assertion in test** — `test_team_decoy_is_never_returned` uses `url is None or "/teams/" not in url`, which passes trivially because the real Raiders-vs-Patriots anchor matches first. The decoy is already excluded by the `-vs-` regex, so the test is fine, but the assertion doesn't isolate the decoy. Optional tightening.

</details>

<details>
<summary>Details</summary>

### Diagnosis matches the diff

The user guessed the source format changed. The coder checked and it didn't: `verification.md` records a live `requests` fetch returning `status 403 len 5485`, `cloudflare_challenge: True`, `has_vs_link: False`, and a replay of the parse logic yielding `None` on that body but the correct `/nfl/las-vegas-raiders-vs-new-england-patriots/` URL on browser-rendered markup. The diff is consistent with that story — `_MATCH_PATTERN = r"/nfl/[a-z0-9-]+-vs-[a-z0-9-]+"` is byte-for-byte unchanged, and the only structural change is where the HTML comes from. A diff that claimed a format change but left the regex alone would be suspicious; this one does not.

### Fetch routed through the existing browser pattern

```
_find_match_url
  ├─ _fetch_listing_html(url, timeout)      # Chromium headless, DoH + _DEFAULT_UA
  │     goto(wait_until="domcontentloaded")
  │     wait_for_timeout(_LISTING_WAIT_MS)  # 12s, deja que CF libere y rendericen
  │     page.content()                       -> html | None
  ├─ if None: requests fallback (make_session + _DEFAULT_UA)   -> html | None
  └─ _pick_match_url(html, ...)              # regex + slug-term, SIN cambios
```

`_fetch_listing_html` mirrors the launch block in `resolve_match` and the two sites in `resolvers.py`: same `_import_playwright("ondemand")`, same `_chromium_host_rules(DEFAULT_DOH_HOSTS)`, same `new_context(user_agent=_DEFAULT_UA)`, same `browser.close()` in a `finally`. The broad `except Exception` returning `None` is the same pattern used elsewhere in the module and makes the fallback fire on any Playwright failure, not just absence. All referenced helpers were confirmed present in `resolvers.py` and `dns_fallback.py` with the signatures used.

### Fallback keeps behavior no worse than before

The `requests` branch is the pre-fix code moved into an `if html is None` block. On a network behind the Cloudflare challenge it 403s and returns `None` (today's behavior, fail-closed); on a network without the challenge it still serves. It costs one request only when the browser fetch already failed, so it adds no latency on the normal path. No retry loops, no new config, no second challenge-solving scheme — the resilience is proportionate, not over-engineered.

### Two browser launches, by choice

Plan step 3 offered to share one `sync_playwright()` session across listing fetch and stream capture. The coder kept them separate (`_fetch_listing_html` launches its own Chromium, `resolve_match` launches another), documented in `verification.md` as a deliberate simplicity trade-off. For a user-initiated tap this is defensible: two short headless launches against the latency a user already expects, in exchange for simpler control flow and a listing fetch that stays independently testable. Not over-engineered; if anything it under-optimizes, which is the safer direction here.

### Offline test and fixture

The parse logic was extracted into `_pick_match_url` purely to make it assertable without the network, and `tests/test_ondemand_find_match.py` runs three checks against `tests/fixtures/streameast_nfl_listing.html`: a live game resolves to its `/nfl/<a>-vs-<b>/` URL, a `/nfl/teams/...-live-streams/` decoy is excluded, and non-matching terms give `None`. The fixture carries the real current anchors plus the decoy. Written as a standalone assert-script because the project has no test runner and `pytest` isn't installed — an acceptable choice the plan explicitly permitted.

Not tested: the browser fetch itself (`_fetch_listing_html`) and the full `resolve_match` → `.m3u8` path, both gated on Playwright which is absent in the dev env. This is the real coverage gap. The parse logic is well covered; the transport change that is the actual fix is only verified by the live-fetch evidence in `verification.md`, not by an automated test, and the end-to-end playback is deferred to the Pi (step 5). That deferral is reasonable given the ephemeral `.m3u8` token needs a live game and the device's network, but it means approval rests on the device check still being performed.

One nit: `test_team_decoy_is_never_returned` asserts `url is None or "/teams/" not in url`. Because the fixture's real Raiders-vs-Patriots anchor matches the given terms, `_pick_match_url` returns that match URL and the assertion passes without ever stressing the decoy. The decoy is genuinely excluded (no `-vs-`, so the regex drops it), so the test isn't wrong, but it doesn't prove what its name claims. Optional.

### Scope

`config/sources.yaml` untouched — correct, the on-demand path doesn't read it and the hybrid path that does is out of scope. The `setup/README.md` change (documenting `09-time-sync.sh`) is unrelated to the resolver but is harmless doc-only context tied to the user's earlier time-sync thread; it bundles a bit of noise into the commit but introduces no risk. The `_resolve_hybrid` Cloudflare twin is correctly identified as out of scope and flagged in the commit body for separate scheduling.

</details>

<details>
<summary>File map</summary>

- `scraper/ondemand.py` — new `_fetch_listing_html` (Playwright listing fetch), parse logic extracted verbatim into `_pick_match_url`, `_find_match_url` now browser-first with `requests` fallback; new `_LISTING_WAIT_MS` constant.
- `tests/test_ondemand_find_match.py` — new standalone assert-script exercising `_pick_match_url` against a fixture (live match, decoy exclusion, non-match).
- `tests/fixtures/streameast_nfl_listing.html` — trimmed real listing markup with a `/nfl/teams/...` decoy.
- `setup/README.md` — doc-only: adds `09-time-sync.sh` to the setup steps and table (unrelated to the resolver).
- `.agents/tasks/fix-ondemand-resolver/verification.md` — recorded verification evidence.

Full diff: `git diff HEAD~1` in `/home/karlos/Kiro/scrapper`.

</details>
