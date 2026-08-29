# Report and dashboard format

Two deliverables per run. `report.py` writes both; `build_dashboard.py` turns the data
into the publishable page.

## `SEO-REPORT-<date>.md`

Written to the **audited repo root** — it is the one artifact that belongs in the user's
project. Everything else (crawl.json, findings.json, vitals.json) stays in the
scratchpad.

Sections, in order:

1. **Header** — date, source path, page count, crawl mode. The reader must be able to
   tell what was covered without reading further.
2. **Tier notice** — the MEASURED / INFERRED / HANDOFF statement, verbatim.
3. **Health summary** — counts by severity.
4. **Repair gates** — A, B, C with status and the rule each enforces.
5. **Deliberate exclusions** — noindex pages that are meant to be noindexed. Listing
   these prevents the reader assuming they were missed.
6. **Findings by stage** — one table per stage: severity, rule id, pages, finding,
   evidence. Stage order, never severity order across stages: the ordering *is* the
   recommendation.
7. **Core Web Vitals** — only when vitals ran, with the lab caveat above the table.
8. **Keyword→page map** — one row per indexable page. Volume and difficulty columns do
   not exist in this table, deliberately: there is nowhere to write a guess.
9. **Human action list** — every HANDOFF with the exact report to open.
10. **How to re-run** — the literal commands.

## Dashboard artifact

`assets/dashboard.html` is a template with a `/*__DATA__*/ null` placeholder.
`build_dashboard.py` replaces it with the run's JSON.

Publish with the `Artifact` tool. Redeploy to the **same file path** on later runs so
the URL stays stable and the user's link keeps working.

### Constraints already handled in the template — do not undo them

- **Fully ASCII output.** `build_dashboard.py` dumps JSON with `ensure_ascii=True`, and
  the template's script uses `\uXXXX` escapes rather than literal `—` / `·`. This is not
  fussiness: crawled content routinely contains em dashes, currency symbols, and
  non-Latin script, and a host that omits `charset=utf-8` renders all of it as mojibake.
  An all-ASCII page is correct under any charset.
- **`</` is split** in the injected payload so a closing tag inside crawled content
  cannot terminate the script block early.
- **Three-state theming.** Full light palette on bare `:root`; dark tokens repeated in
  both `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` and
  `:root[data-theme="dark"]`. Never define a color only inside a media or `[data-theme]`
  block. `body` sets an explicit token background.
- **Semantic severity color is separate from the accent.** Critical/high/medium/low have
  their own token pairs (`--sev-*` and `--sev-*-bg`); the indigo accent is never used to
  signal severity.
- **Wide content scrolls in its own container.** The stage rail and every table sit in
  `overflow-x: auto` wrappers, so the page body never scrolls sideways. Verified at
  1280px and 375px.

### Structure

The stage rail is the page's thesis: seven stages left to right with the three gates
drawn *between* them, so the dependency order is visible before any finding is read.
Stage numbering is used because the stages genuinely are a sequence — it encodes real
information, not decoration.

Then: severity tiles → gates → vitals → findings by stage → pages → handoffs.
Summary before detail, because a dashboard is scanned rather than read.

## Reporting tone

State what was measured and what it means. No hedging on facts the crawl established,
no confidence on things it did not. When a finding is medium-confidence (near-duplicate,
cannibalization, thin content), say so — those need a human read before action.

Never present a total finding count as a score or a grade. Twenty-nine low-severity
findings on a healthy site is not worse than three critical ones on a broken one, and a
single number invites exactly that misreading.
