---
name: sitemend
description: "Crawl-driven SEO audit, repair planning, and implementation for a local site codebase or its dev server. Use when the user asks to audit, fix, or improve SEO; find indexing/crawl problems, orphan pages, duplicate content, keyword cannibalization, thin pages, broken internal links, missing or misleading schema, Core Web Vitals, or mobile usability; or to re-verify SEO after changes. Runs a real crawler and real Lighthouse, proposes every edit before applying it, and never invents search volume, keyword difficulty, or backlink data."
version: 1.7.0
---

# sitemend

An SEO **repair system**, not a checklist. It crawls the site, produces evidence-backed
findings in dependency order, proposes fixes, and re-verifies after they land.

The ordering is the whole point:

```
Crawl → Index → Architecture → Content → On-Page → Performance → Schema → Authority → Monitor
```

Work done out of order is wasted. Optimising a title on a page Google cannot index
changes nothing. Writing schema for a page that is about to be merged away is thrown
out with the page. Building links to either is the most expensive mistake of all.
Three **gates** below make that ordering unskippable.

---

## Two rules that override everything else

**1. Never apply an edit without approval.** This skill audits and proposes. It writes
source files only after the user approves a specific batch. `audit` and `plan` never
modify the site.

**2. Never state a number you did not measure.** Search volume, keyword difficulty,
backlink counts, and field Core Web Vitals are not observable from a codebase. They
become HANDOFF actions naming the exact report to open — never an estimate, never a
plausible-looking figure. See [`references/capability-tiers.md`](references/capability-tiers.md).

---

## Verbs

| Invocation | Does | Writes |
| --- | --- | --- |
| `sitemend audit <path\|url>` | Crawl → audit → report + dashboard. Establishes the **baseline**. | report only |
| `sitemend plan` | Ranks findings into stage-ordered batches; previews the exact diff for each | nothing |
| `sitemend apply <batch-id>` | Applies **one approved batch**, then re-checks that stage | source files |
| `sitemend verify` | Re-crawl, diff vs. baseline, regression gate, GSC handoff list | report only |

Bare invocation with no verb → `audit`. `plan` refuses without a baseline; `apply`
refuses without an approved plan and without a named batch id.

---

## The gates

Enforced in code (`audit.py` skips gated findings) **and** in conversation. When the
user asks to skip ahead, name the gate, say what blocks it, and say what would clear
it. Do not silently comply, and do not silently refuse.

| Gate | Rule | Why |
| --- | --- | --- |
| **A** — Index before content | No stage 2+ finding is raised for a page stage 1 marked non-indexable | A title Google never reads is not worth writing. Raising it also buries the indexability problem that actually matters. |
| **B** — Architecture before schema | No schema batch for a page flagged orphaned or as a merge candidate | Markup on a page about to be deleted or consolidated is discarded work. |
| **C** — Everything before authority | Stage 6 off-page work is refused until stages 1–5 verify clean | Links pointing at pages Google can't crawl, index, or understand are the most expensive form of wasted effort. |

Gate B has a third state, `NOT ASSESSED`. Only fs mode enumerates every page, so only fs
mode can *prove* a site has no orphans; an http crawl reaches pages via links and the
sitemap, so silence from it is absence of evidence, not evidence of absence. In http mode
with no orphan findings the gate reports `NOT ASSESSED` and names the fs re-run that would
clear it. `BLOCKED` outranks both — an orphan the http walk did see is real either way.

---

## Do no harm

Non-negotiable. These hold even when the user asks directly; if they reaffirm after
being told the risk, say what you are doing and proceed, but never do these silently.

1. **Never change a URL without a redirect plan in the same batch.** A renamed URL
   without a 301 discards every signal that page had earned.
2. **Never add `noindex`, delete a page, or merge content without explicit approval.**
   All three are hard to reverse and can remove a ranking page outright.
3. **Never write structured data asserting something the page does not show.** No
   invented ratings, review counts, prices, or `aggregateRating`. `schema_check.py`
   actively tests for this and will flag your own output.
4. **Never fabricate author bios, credentials, or E-E-A-T signals.** Ask for real ones.
   A fabricated author is worse than no author.
5. **Never invent metrics, testimonials, or trust claims** in any copy you generate.

Rules 3–5 are the same honesty standard the rest of this skill applies to itself: a
tool that refuses to guess at keyword volume must not guess at a person's credentials.

---

## Running it

Scripts live in `scripts/` and need Python 3 with `beautifulsoup4` + `lxml`. Write all
run artifacts to the **scratchpad**, never the user's repo — only the final report goes
into the repo.

```bash
cd <skill>/scripts
python crawl.py        --root "<repo>" --out "<scratch>/crawl.json"
python schema_check.py --crawl "<scratch>/crawl.json" --out "<scratch>/schema.json"
python audit.py        --crawl "<scratch>/crawl.json" --schema "<scratch>/schema.json" \
                       --out "<scratch>/findings.json"
python report.py       --crawl "<scratch>/crawl.json" --findings "<scratch>/findings.json" \
                       --outdir "<repo>"
```

**Core Web Vitals** need a *running* server. Start it with the Browser pane's
`preview_start` (never a bare Bash dev server), then:

```bash
python vitals.py --base http://localhost:PORT --pages-json '["/","/about/"]' --out "<scratch>/vitals.json"
```

Pass `--vitals` to `audit.py` to fold stage-4 findings in.

**Order matters**: `schema_check.py` runs *before* `audit.py`, which merges its output.
`findings.json` is then the complete truth and the gates are computed over all of it.
Merging schema later in `report.py` left the gates blind to stage-5 problems and
double-reported every schema finding, so `report.py` now refuses `--schema`.

### Two environment traps, both already handled

- **Lighthouse exits non-zero on Windows** when chrome-launcher fails to delete its
  temp profile (`EPERM`), even though the report wrote correctly. `vitals.py` judges
  success by parsing the report, never by exit code. Do not "fix" this by trusting the
  exit code.
- **Git Bash rewrites bare `/` arguments** into Windows paths (`C:/Program Files/Git/`).
  Always pass pages via `--pages-json`. `vitals.py` detects the mangling and stops with
  a clear message rather than silently measuring a nonsense URL.

### Tests

Run before and after changing anything in `scripts/`:

```bash
python tests/test_skill.py
```

146 tests (two are opt-in: real Lighthouse and real Chrome rendering), no fixture files -- each builds a throwaway site in a temp directory. They
pin down calibration decisions made after real false positives (build-dir exclusion,
deliberate noindex, finding grouping, title thresholds, heading order, schema claim
corroboration, gate boundaries). A failure means "was this behaviour meant to change",
not "make it pass".

### Crawl modes

- **fs** (default) — walks the repo. Handles both URL conventions: clean-URL
  directories (`about/index.html` → `/about/`) and flat files (`services.html` →
  `/services.html`). Build output (`dist/`, `build/`, `_site/`, `node_modules/`) is
  excluded; without that, a `dist/` copy makes every real page look like duplicate
  content.
- **http** — crawls a running dev server or live URL for real status codes and
  redirects. Use this when indexability or redirect behaviour is the question. The
  frontier is seeded from the sitemap (robots.txt `Sitemap:` lines, else `/sitemap.xml`,
  else `/sitemap-index.xml`, indexes followed) as well as `--start`, so a page the sitemap
  lists but nothing links to is still fetched; URLs that 404 are recorded as skipped
  rather than admitted as pages, which is what keeps `S1-SITEMAP-ORPHAN` and
  `S1-BROKEN-LINK` meaningful. A page that is neither linked nor listed is unreachable
  over HTTP by construction — that is why fs is the default, and why Gate B reports
  `NOT ASSESSED` rather than `CLEAR` here. Only http mode sees redirect chains and
  response headers.
  Add `--render` (needs a local Chrome/Chromium/Edge; `--render-limit` caps it at 50
  pages, ~5 s each) to parse the post-JavaScript DOM instead of the raw HTML. It is the
  only way to prove `S1-JS-DEPENDENT` and `S1-JS-HEAD-MISMATCH`; without it a
  client-rendered page is only hinted at by `S1-JS-SHELL`. Say how many pages were
  rendered (`meta.rendered_pages`) when reporting.

---

## Flow

**`audit`** — crawl, audit, schema-check, optionally vitals, then report. Present the
health summary, the gate status, and the top findings by severity. State the crawl mode
and page count so the user knows what was and was not covered. Mention what was
deliberately excluded (build dirs, noindex utility pages) rather than staying silent.

**`plan`** — read `findings.json`, group into stage-ordered batches, and for each batch
show the concrete edit you intend to make, per file. Order batches by stage, never by
convenience. Say which gates block which batches. Then stop and ask which to apply.

**`apply <batch-id>`** — apply only that batch. Re-run `crawl.py` + `audit.py` and
confirm the targeted findings closed. Report honestly if any did not.

**`verify`** — re-crawl and run `verify.py` against the baseline. The gate FAILS if any
new critical/high finding appeared. Report closed / new / persisting counts, then hand
over the GSC action list.

---

## Progressive disclosure

Load a reference only when the stage is actually in play.

| File | Read when |
| --- | --- |
| [`references/pipeline.md`](references/pipeline.md) | Always — stage order, gate conditions, batch rules |
| [`references/capability-tiers.md`](references/capability-tiers.md) | Always — MEASURED / INFERRED / HANDOFF contract |
| [`references/fix-recipes.md`](references/fix-recipes.md) | During `plan` and `apply` — the exact edit per finding id |
| [`references/serp-research.md`](references/serp-research.md) | Stage 3 keyword/intent work with WebSearch |
| [`references/stages.md`](references/stages.md) | Stage-by-stage detail: what each check means and how to fix it |
| [`references/report-format.md`](references/report-format.md) | Building the report or the dashboard artifact |

---

## Scope — what this skill deliberately does not cover

If a skill covering one of these is installed, hand off to it rather than improvising:

- **AEO / GEO / getting cited by LLMs.** This skill covers classic crawl/index/rank only.
- **Greenfield information architecture** — deciding what pages a new site should have.
  `sitemend` audits and repairs an architecture that already exists.
- **Advisory review of content you cannot crawl.** `sitemend` needs a codebase or a
  reachable URL, because every finding it makes must cite evidence.
