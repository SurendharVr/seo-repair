# Stage detail — what each check means

Reference for interpreting findings. Fixes live in `fix-recipes.md`.

---

## Stage 1 — Crawl & Indexing

| Finding | Means | Severity logic |
| --- | --- | --- |
| `S1-ROBOTS-MISSING` | No `/robots.txt` | high — crawlers get no directives and no sitemap pointer |
| `S1-SITEMAP-MISSING` | No `/sitemap.xml` | high — discovery relies entirely on link-following |
| `S1-ROBOTS-NO-SITEMAP` | robots.txt exists but declares no `Sitemap:` | medium |
| `S1-SITEMAP-ORPHAN` | Sitemap lists a URL that doesn't exist | high — submitting 404s wastes crawl budget and signals neglect |
| `S1-SITEMAP-GAP` | Indexable page absent from sitemap | medium |
| `S1-INDEX-CONTRADICTION` | Page is in the sitemap **and** blocked | **critical** — the site contradicts itself |
| `S1-NOT-INDEXABLE` | Blocked, and not obviously on purpose | medium |
| `S1-CANONICAL-MISSING` | No canonical on an indexable page | medium |
| `S1-CANONICAL-CROSS` | Canonical points at a different page | high — may be deliberate; ask |
| `S1-CANONICAL-BROKEN` / `-OFFSITE` | Canonical points to a non-existent URL or another origin | critical |
| `S1-BROKEN-LINK` | Internal link to a URL that doesn't exist | high |
| `S1-URL-UNCLEAN` | Uppercase, underscores, or spaces in a URL | medium |
| `S1-URL-MIXED-CONVENTION` | Site mixes `/page` and `/page.html` | medium, low confidence |

**Deliberate exclusions.** A `noindex` on a 404, or on a page also disallowed in
robots.txt, is a decision. Reported separately as a confirmation, never as a defect.
Utility pages matched: `404`, `403`, `500`, `offline`, `thank-you`.

**JS-rendered content** is checked outside the crawler — see `pipeline.md`.

---

## Stage 2 — Architecture

| Finding | Means |
| --- | --- |
| `S2-ORPHAN` | Zero internal links point here. Google may never find it, and it receives no internal authority. |
| `S2-UNREACHABLE` | No path from the homepage by following links. Stronger than orphan. |
| `S2-LOW-INLINKS` | Exactly one inbound link. Weak signal for a page that matters. |
| `S2-DEEP` | More than 3 clicks from home. Depth correlates with reduced crawl frequency. |
| `S2-H1-MISSING` / `-MULTIPLE` | No H1, or several competing ones. |
| `S2-HEADING-SKIP` | Level jumps (H3→H5). Accessibility issue first, structure signal second. |
| `S2-BREADCRUMB-MISSING` | No `BreadcrumbList` on a non-home page. |

A finding on many pages with identical evidence is usually **one shared component**
(header, footer, nav). It is grouped into a single finding with an `affects` count —
one fix, not N fixes. Check whether the file is shared before estimating effort.

---

## Stage 3 — Content & On-Page

| Finding | Means |
| --- | --- |
| `S3-DUPLICATE-EXACT` | Byte-identical main content. Critical: Google picks one and the other's effort is wasted. |
| `S3-NEAR-DUPLICATE` | ≥80% estimated Jaccard similarity. Medium confidence — read both before merging. |
| `S3-CANNIBALIZATION` | ≥60% overlap in head terms across title/H1/description, after dropping template words (terms on ≥35% of pages, minimum 4). Suppressed when the pair is already a duplicate — same root cause. |
| `S3-THIN` | Under 250 words of *main* content (nav/header/footer excluded). Verify against the rendered DOM first. |
| `S3-TITLE-MISSING` / `-DUPLICATE` / `-SHORT` / `-LONG` | Target 50–60 chars; only flagged past 65, since the real limit is pixel width. |
| `S3-DESC-*` | Target 70–160 chars. Not a ranking factor — it is SERP ad copy. |
| `S3-IMG-ALT-MISSING` | No `alt` **attribute**. `alt=""` on a decorative image is correct and not flagged. |
| `S3-IMG-DIMS-MISSING` | No width/height — layout-shift risk. |
| `S3-IMG-HEAVY` | Over 200 KB. Covers `<img src>`, `<img srcset>` and `<picture>/<source>` candidates — all are real downloads. |
| `S3-ASSET-UNUSED` | Media files nothing references. Dead weight in the deploy package. Suppressed if the filename appears anywhere in any page's source, so false positives are unlikely — but verify before deleting. |
| `S3-VIEWPORT-MISSING` | Critical — the page is not mobile-friendly at all. |
| `S3-LANG-MISSING` | No `lang` on `<html>`. |

### E-E-A-T

Not mechanically checkable, and deliberately so. What the skill may do: point out that
a page making professional claims has no named author, no credentials, and no contact
path. What it must **never** do: write a biography, invent credentials, or manufacture
"trusted by" claims. Ask the user for real details or leave the gap visible.

---

## Stage 4 — Performance & Mobile

Lighthouse thresholds used (`audit.py`):

| Metric | Good | Poor |
| --- | --- | --- |
| LCP | ≤ 2500 ms | > 4000 ms |
| CLS | ≤ 0.1 | > 0.25 |
| TBT | ≤ 200 ms | > 600 ms |

TBT is a **lab proxy** for INP, not INP itself. INP needs field data (HANDOFF).

Everything here is tagged `MEASURED-lab`. A localhost run has no CDN, no real network,
no real device — extrapolating to production is INFERRED and must be labelled so.

**Mobile usability** is checked in the Browser pane: `resize_window` to 375px, reload,
then assert no horizontal overflow —
`document.documentElement.scrollWidth > window.innerWidth`.

---

## Stage 5 — Structured Data

| Finding | Means |
| --- | --- |
| `S5-SCHEMA-INVALID-JSON` | A JSON-LD block doesn't parse — it is entirely ignored by Google. |
| `S5-SCHEMA-MISSING` | No structured data on an indexable page. |
| `S5-SCHEMA-REQUIRED-MISSING` | A required property for that type is absent. A nested `@id` reference is not checked on its own when the full node is defined elsewhere on the site. |
| `S5-SCHEMA-RECOMMENDED-MISSING` | Recommended property absent. Low. |
| `S5-SCHEMA-UNSUPPORTED-CLAIM` | **The important one.** Markup asserts content not corroborated on the site. |
| `S5-SCHEMA-CLAIM-OFFPAGE` | Content is elsewhere on the site but not this page. Usually fine for a shared business block. |

**Every stage-5 finding comes from `schema_check.py`**, merged into `findings.json` by
`audit.py --schema`. `audit.py` has no schema logic of its own — an inline copy there
could not corroborate claims against page text and double-reported through the merge.

### How claim verification works, and why it is scored this way

Naive substring matching produces false positives, because markup legitimately
reformats what the page renders ("St" vs "Street", an address split across elements).
So claims are scored by **token coverage**, and severity depends on where the support
lives: on this page → no finding; elsewhere on the site → low; nowhere → high.

**Reviews get a stricter, separate check.** A token bag hides fabricated reviews:
generic words in a testimonial ("good", "order", "100") match elsewhere on the site and
inflate the score, while the reviewer's *name* — the thing that makes it verifiable —
appears nowhere. So each review's `author.name` is checked directly against the page
text, and any author absent from the page is reported by name.

This matters because review and rating markup that doesn't correspond to reviews
visible on the page violates Google's structured data policy and risks a manual action.

---

## Stage 6 — Authority

**Advisory only.** This skill cannot measure a backlink profile or build links, and it
does not pretend otherwise. It produces a human action list:

- Which pages most deserve links (commercial pages with weak internal support).
- Legitimate opportunities to research: industry directories, local citations, supplier
  and partner pages, genuine press, real community participation.
- For local businesses: Google Business Profile completeness and NAP consistency —
  usually higher leverage than any link.
- **Never** suggests paid links, PBNs, link exchanges, comment/forum spam, or any
  scheme that violates Google's spam policies.

Disavow only for a genuine, confirmed unnatural-links manual action. Disavowing on
suspicion does more harm than good.

Gated behind stages 1–5 (Gate C).

---

## Stage 7 — QA & Monitoring

`verify.py` diffs a fresh run against the baseline: **closed**, **new**, **changed**
(same rule and pages, different evidence), **persisting**. The gate **fails** if any new
critical or high finding appeared — fixing a title while orphaning a page is not a win.

Then hand off to Search Console: request indexing for changed URLs, watch index
coverage, and check field Core Web Vitals after roughly 28 days of data.
