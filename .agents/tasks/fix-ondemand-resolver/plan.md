# Fix plan: on-demand resolver returns None ("Stream no disponible aún")

## Scope
Fix ONLY the on-demand stream resolver `resolve_match` in
`/home/karlos/Kiro/scrapper/scraper/ondemand.py`. Do not refactor the batch
scraper or the UI. Keep the existing architecture and the Spanish-commented
style.

## Root cause (confirmed live from this environment)

The user suspected the source HTML/format changed. It did **not**. The failure is
in how the listing is fetched, not in how it is parsed.

What was verified live (jueves, red con salida a internet):

1. `_find_match_url` downloads the listing with the `requests`-based session
   (`make_session()` + `_DEFAULT_UA`). Fetching
   `https://v2.streameast.ga/nfl-streams/` that way returns **HTTP 403** with a
   Cloudflare "managed challenge" interstitial, NOT the listing. The body is the
   JS/cookie challenge page:

   ```html
   <!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>
   ... <span id="challenge-error-text">Enable JavaScript and cookies to continue</span>
   ... window._cf_chl_opt = {... cType: 'managed', cZone: 'v2.streameast.ga', ...}
   ```

   `requests` cannot solve the challenge, so `resp.raise_for_status()` raises on
   the 403 (or, if it didn't, the parsed challenge HTML contains zero
   `/nfl/...-vs-...` links). Either way `_find_match_url` returns `None`,
   `resolve_match` returns `None`, and the UI shows "Stream no disponible aún".
   This matches the symptom exactly: the ESPN agenda cards work (ESPN is a
   different API, fetched elsewhere), but tapping a card fails.

2. The **real listing markup is unchanged**. When the same URL is opened with
   Playwright/Chromium (a real browser passes the Cloudflare challenge), the page
   title is `NFL Live Streams - StreamEast` and the per-match anchors are exactly
   the shape the current regex already expects. Representative current markup:

   ```html
   <a aria-label="Las Vegas Raiders vs New England Patriots"
      class="m-card__link"
      href="/nfl/las-vegas-raiders-vs-new-england-patriots/"></a>
   ```

   Representative current per-match hrefs from today's rendered page:

   ```
   /nfl/las-vegas-raiders-vs-new-england-patriots/
   /nfl/chicago-bears-vs-green-bay-packers-1/
   /nfl/denver-broncos-vs-los-angeles-chargers/
   /nfl/new-york-giants-vs-washington-commanders/
   ```

3. The existing `_MATCH_PATTERN = r"/nfl/[a-z0-9-]+-vs-[a-z0-9-]+"` and the
   slug-term matching in `_find_match_url` were replayed against the
   browser-rendered HTML: 12 real match links are found and a live game
   (`lv/las/vegas/raiders` vs `ne/new/england/patriots`) resolves correctly to
   `https://v2.streameast.ga/nfl/las-vegas-raiders-vs-new-england-patriots/`.
   Note the page also contains `/nfl/teams/<team>-live-streams/` links; the
   regex correctly ignores them because they lack `-vs-`.

Conclusion: the regex/selector are fine. The only broken step is fetching the
listing with `requests`, which Cloudflare now blocks. The fix is to fetch the
listing with the browser that the module already imports and uses for the stream
capture step.

### Secondary observation (OUT OF SCOPE, note only)
`scraper/resolvers.py` `_resolve_hybrid` (the hourly batch pre-scrape) fetches
the same listing with `requests` + `make_session` and therefore has the same
Cloudflare 403 problem. It is not part of this task. Mention it in the commit
body / findings so it can be scheduled separately; do not change it here.

## The minimal change

Make `_find_match_url` obtain the listing HTML via Playwright instead of
`requests`, then keep the existing BeautifulSoup + `_MATCH_PATTERN` + slug-term
logic exactly as-is. Reuse the browser launch pattern already present in
`resolve_match` (same DoH host rules, same `_DEFAULT_UA`) so the two steps share
one approach. No regex changes. No `config/sources.yaml` change is needed,
because `ondemand.py` uses its own hardcoded constants and the patterns there are
still correct; leave `sources.yaml` untouched to avoid touching the unrelated
hybrid path.

Chosen approach and rationale: fetch the listing inside the same
`sync_playwright()` session already opened in `resolve_match`, before navigating
to the match page. This passes the Cloudflare challenge the same way the stream
capture already does, keeps a single browser launch, and leaves the parsing code
untouched. The alternative (bolting a Cloudflare-bypass onto `requests`) is more
fragile and duplicates browser logic the module already has — rejected.

Resilience: if Playwright is unavailable or the listing fails to render, fall
back to the existing `requests` fetch (it will usually 403, but on networks
without the challenge it still works and costs nothing). This keeps behavior no
worse than today and avoids a hard dependency change.

## Implementation steps

- [ ] 1. Add a Playwright-based listing fetch helper in
      `scraper/ondemand.py`. New function
      `_fetch_listing_html(listing_url, timeout) -> str | None` that: launches
      Chromium headless with `_chromium_host_rules(DEFAULT_DOH_HOSTS)` and
      `user_agent=_DEFAULT_UA` (same as `resolve_match`), does
      `page.goto(listing_url, wait_until="domcontentloaded")`, waits a few
      seconds (reuse a small constant, e.g. `_LISTING_WAIT_MS = 12000`, with a
      Spanish comment explaining it is to let Cloudflare's challenge clear and
      the match cards render), returns `page.content()`, and returns `None` on
      any exception (logged with `log.warning`). Import `sync_playwright` via the
      existing `_import_playwright("ondemand")`.
      Files: `scraper/ondemand.py`
      Verify: `python -c "import ast; ast.parse(open('scraper/ondemand.py').read())"`
      parses clean; see step 4 for the behavioral check.

- [ ] 2. Change `_find_match_url` to use the rendered HTML. Replace the
      `requests` fetch block with: first call `_fetch_listing_html(...)`; if it
      returns HTML, parse that. If it returns `None`, fall back to the current
      `make_session()` + `_DEFAULT_UA` + `session.get(...).raise_for_status()`
      path (keep that code as the fallback branch). Keep the BeautifulSoup
      parsing, `_MATCH_PATTERN.search`, and the `any(t in slug ...)` slug-term
      logic byte-for-byte. Keep the Spanish docstring/comments; add one line
      noting the browser fetch is needed because the listing is behind Cloudflare.
      Files: `scraper/ondemand.py`
      Verify: step 4.

- [ ] 3. (Optional, cheap) To avoid launching Chromium twice per tap, have
      `resolve_match` open the browser once and reuse it: fetch the listing HTML
      from a page in the same `sync_playwright()` context, then navigate to the
      match page in that same context. Only do this if it stays simple; if it
      complicates the control flow, keep step 1's standalone helper (two short
      browser launches is acceptable for an on-demand, user-initiated action).
      State in the commit which option you took and why.
      Files: `scraper/ondemand.py`
      Verify: step 4.

- [ ] 4. Add a focused OFFLINE unit check using a saved fixture of today's real
      listing markup, since the live source sits behind Cloudflare and cannot be
      asserted deterministically in CI. Create
      `tests/test_ondemand_find_match.py`. Save a trimmed fixture
      `tests/fixtures/streameast_nfl_listing.html` containing a handful of the
      real current anchors (reuse the representative markup quoted above,
      including at least one `/nfl/teams/...-live-streams/` decoy that must NOT
      match). The test calls the parsing logic against the fixture (either by
      monkeypatching `_fetch_listing_html` to return the fixture text, or by
      factoring the parse-from-html body of `_find_match_url` into a tiny pure
      helper `_pick_match_url(html, listing_url, a_terms, b_terms)` and testing
      that). Assert: (a) a live game's terms resolve to the correct
      `/nfl/<a>-vs-<b>/` URL; (b) a `/nfl/teams/...` decoy is never returned;
      (c) non-matching terms return `None`.
      Files: `tests/test_ondemand_find_match.py`,
      `tests/fixtures/streameast_nfl_listing.html`, and (if the refactor route is
      taken) `scraper/ondemand.py`.
      Verify: from the repo root in the project venv,
      `PYTHONPATH=. python -m pytest tests/test_ondemand_find_match.py -q`
      passes. (`pytest` is not currently a dependency — if it is not installed,
      either add it to a dev section of `requirements.txt` or write the test as a
      plain `python tests/test_ondemand_find_match.py` script using `assert`; the
      project has no existing test runner, so a standalone assert-script is an
      acceptable minimal choice. State which you chose.)

- [ ] 5. Live end-to-end verification ON THE DEVICE (the Raspberry Pi, where
      network + DoH + a real display exist). This cannot be fully asserted from
      the dev environment because the stream-capture step needs the Pi's network
      and the m3u8 token is live. Run, with the kiosk stopped and the venv
      active:
      ```
      cd ~/scrapper
      PYTHONPATH=~/scrapper ~/scrapper-venv/bin/python - <<'PY'
      from scraper.ondemand import resolve_match
      # usar los términos de un partido EN VIVO hoy (ej. Raiders vs Patriots)
      r = resolve_match(["lv","las","vegas","raiders"],
                        ["ne","new","england","patriots"])
      print(r)
      PY
      ```
      Expected: a `StreamResult(url="https://....m3u8", referrer="https://v2.streameast.ga/")`
      for a game that is actually live, instead of `None`. Use team terms for a
      game currently in progress (a game not yet started legitimately returns
      `None`). Confirm in the UI that tapping a live card now plays instead of
      showing "Stream no disponible aún".
      Files: none (verification only).

## Notes / assumptions
- The user's time-sync concern (message history) is already resolved; the agenda
  cards show correct live scores. This plan does not touch time sync or the ESPN
  agenda.
- No change to `config/sources.yaml`: the on-demand path uses the hardcoded
  constants in `ondemand.py`, and those patterns are still correct against
  today's markup.
- The parallel Cloudflare issue in `_resolve_hybrid` is intentionally left for a
  separate task; flag it in the commit body.
- The fix was diagnosed with a live fetch from this environment; the regex/parse
  logic was replayed against the real rendered HTML. The browser-fetch fix is the
  minimal change that restores extraction. Final confirmation that a live stream
  plays must happen on the device (step 5).
```
