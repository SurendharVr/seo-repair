# The pipeline — stage order, gates, and batching

## Why order matters

The checklist items are not independent. Each stage's output is the next stage's input,
and doing them out of order destroys work:

| If you skip ahead to… | …before fixing | What happens |
| --- | --- | --- |
| Titles and meta descriptions | Indexability | Google never reads them. Zero effect. |
| Schema markup | Consolidation decisions | Markup is deleted along with the merged page. |
| Content expansion | Cannibalization | You strengthen the page that should have lost. |
| Backlinks | Everything | Authority flows into pages Google can't crawl, index, or understand. Most expensive possible mistake. |
| Performance tuning | Duplicate/orphan cleanup | You optimise pages that shouldn't exist. |

## The stages

**0 · Crawl.** `crawl.py` builds `crawl.json`. Everything downstream reads it and only
it. If the crawl is wrong, every stage is wrong — so validate the page count and URL
mapping before trusting any finding.

**1 · Index.** Can Google reach and index this page at all? robots.txt, sitemap.xml,
`noindex`, canonicals, broken links, URL hygiene, JS-rendered content. *Nothing else
matters until this is clean.*

**2 · Architecture.** Orphans, click depth, internal-link distribution, breadcrumbs,
heading structure, consolidation candidates. Decides which pages will exist.

**3 · Content & on-page.** Duplicates, cannibalization, keyword→page mapping, titles,
descriptions, alt text, E-E-A-T. Operates only on pages that survived stage 2.

**4 · Performance & mobile.** Core Web Vitals (lab), mobile usability, image weight.

**5 · Schema.** Type selection, required properties, and — most importantly —
verification that the markup does not assert things the page never shows.

**6 · Authority.** Off-page. **Advisory only.** This skill produces a human action list;
it does not and cannot build links. Gated behind stages 1–5.

**7 · QA & monitor.** Re-crawl, regression gate, Search Console handoff.

## The gates, precisely

**Gate A — Index before content.**
Condition: page has `noindex`, an `X-Robots-Tag: noindex`, or is disallowed in
robots.txt.
Effect: `audit.py` builds `live = [pages that are indexable]` and every stage 2+ check
iterates `live`, not `pages`. A blocked page therefore produces stage-1 findings only.
Clearing it: make the page indexable, or accept it as a deliberate exclusion.

The boundary is stage 2, not stage 3: a missing H1 or a heading skip on a page Google
will never index is noise that buries the one finding that does matter for it. This is
enforced uniformly -- the graph checks and the per-page checks both honour it.

**Gate A exception — deliberate exclusions.** A `noindex` on a 404, an error page, or a
page the author also disallowed in robots.txt is a *choice*, not a defect. These are
reported under "deliberate exclusions", not as findings. The one case that is always a
defect is a page that is in the sitemap **and** blocked — the site contradicting itself
(`S1-INDEX-CONTRADICTION`, critical).

**Gate B — Architecture before schema.**
Condition: page is flagged `S2-ORPHAN`, `S2-UNREACHABLE`, `S3-DUPLICATE-EXACT`, or
`S3-NEAR-DUPLICATE`.
Effect: do not propose a schema batch for it. Resolve the merge/link decision first.

**Gate B has three states, not two.** A gate may only be *cleared* by a check that could
actually run. fs mode enumerates the filesystem, so it can prove a site has no orphans.
An http crawl cannot: it discovers pages via links and `sitemap.xml`, so a page that is
neither linked nor listed is invisible to it, and silence there is absence of evidence
rather than evidence of absence. In http mode with no orphan findings, Gate B reports
**`NOT ASSESSED`** and names the fs re-run that would clear it. `BLOCKED` still outranks
it — an orphan the http walk *did* see is real either way. Same principle as the report's
per-stage `assessed` state: a check nobody could run is not a check that passed. Do not
"simplify" this back to a findings-presence test; that version once reported "no orphan
or duplicate pages" on a site with two orphans.

**Gate C — Everything before authority.**
Condition: any critical or high finding remains open in stages 1–5.
Effect: refuse stage 6. Name the blocking findings.

When a user asks to skip a gate, say which gate, what blocks it, and what would clear
it — then let them decide. A gate is a strong default, not a refusal to be argued with.

## Batching rules

Batches are stage-ordered and independently approvable.

1. **One stage per batch.** Never mix an indexability fix with a copy rewrite; if the
   batch has to be rolled back, the rollback should be coherent.
2. **Order by stage, then severity.** Never by how easy the fix is.
3. **Split risky from safe.** Mechanical edits (alt text, a missing canonical, a
   sitemap entry) go in one batch. Anything touching URLs, redirects, page merges, or
   deletions goes in its own batch, with the redirect plan included.
4. **Show the diff before applying.** Per file, the exact before/after.
5. **Re-check after applying.** Re-crawl and confirm the targeted findings actually
   closed. Report honestly when one did not.

## Cross-checking JS-rendered content

`crawl.py` records `static_text_len` from the raw HTML. For any site that might render
content client-side, compare against the rendered DOM:

1. `preview_start` the dev server.
2. `get_page_text` on a sample of pages via the Browser pane.
3. Compare with `static_text_len`.

A large gap means content exists only after JS runs, which is a stage-1 crawlability
risk and also means `S3-THIN` word counts are understated. Say which of the two you are
looking at before drawing a conclusion.
