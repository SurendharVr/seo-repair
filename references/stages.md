# Stage detail — what each check means

Reference for interpreting findings. Fixes live in `fix-recipes.md`.

---

## Stage 1 — Crawl & Indexing

| Finding | Means | Severity logic |
| --- | --- | --- |
| `S1-ROBOTS-MISSING` | No `/robots.txt` | low — Google treats a robots.txt that 404s as "crawl everything"; the cost is the missing `Sitemap:` pointer |
| `S1-SITEMAP-MISSING` | No sitemap where a crawler looks: none declared in robots.txt (`Sitemap:` lines, path only, read from the audited origin), none at `/sitemap.xml`, none at `/sitemap-index.xml`. `<sitemapindex>` files are followed to their children, up to 50 sitemap files. A file counts only if it holds `<urlset>` or `<sitemapindex>`, so an app shell served at that path is not a sitemap | medium — discovery relies on link-following |
| `S1-ROBOTS-NO-SITEMAP` | robots.txt exists but declares no `Sitemap:`, and a sitemap was found at a default path (Astro's undeclared `/sitemap-index.xml` is the common case) | medium |
| `S1-SITEMAP-ORPHAN` | Sitemap lists a URL that doesn't exist | high — submitting 404s wastes crawl budget and signals neglect |
| `S1-SITEMAP-GAP` | Indexable page absent from sitemap | medium |
| `S1-INDEX-CONTRADICTION` | Page is in the sitemap **and** blocked | **critical** — the site contradicts itself |
| `S1-NOT-INDEXABLE` | Blocked, and not obviously on purpose | medium; **critical** for the homepage, which is never a deliberate exclusion |
| `S1-CANONICAL-MISSING` | No canonical on an indexable page | medium |
| `S1-CANONICAL-CROSS` | Canonical points at a different page | high — may be deliberate; ask |
| `S1-CANONICAL-BROKEN` / `-OFFSITE` | Canonical points to a non-existent URL or another origin (scheme and host compared, not a string prefix) | critical |
| `S1-PLACEHOLDER-DOMAIN` | Canonicals, `og:url`, sitemap URLs or a robots `Sitemap:` line use a placeholder origin (example.com/.org/.net, `localhost`, `.test` and the like) | high — the base origin is inferred from these same URLs, so a uniformly wrong site looks consistent to every other check |
| `S1-HOST-MISMATCH` | Canonicals, `og:url`, sitemap `<loc>`s, the robots `Sitemap:` line and the base URL do not all name one host (a `*.vercel.app` / `*.netlify.app` preview host in a production build, or www against the apex). Hosts are compared, not schemes. A base URL on localhost or a preview host (an http crawl of a dev server or a preview deploy) is left out, so correct production URLs there are not reported; placeholder hosts are left to `S1-PLACEHOLDER-DOMAIN`, and canonicals alone on another origin to `S1-CANONICAL-OFFSITE` | **critical** when one of the hosts is a hosting platform's preview host that differs from the base URL. **medium** when every published URL agrees on one host and only the base URL differs ("served on X, but every URL names Y; check X redirects to Y"): that is a deliberate canonical host, and the per-page `S1-CANONICAL-OFFSITE` findings fold into it. Otherwise high |
| `S1-SOFT-404` | http mode only: one random path that cannot exist (`/sitemend-404-probe-…`) answered 200, directly or after a redirect | high — every mistyped or deleted URL becomes an indexable copy of the error page |
| `S1-BROKEN-LINK` | Internal link to a URL that doesn't exist | high |
| `S1-URL-UNCLEAN` | Uppercase, underscores, or spaces in a URL | medium |
| `S1-URL-MIXED-CONVENTION` | Site mixes `/page` and `/page.html` | medium, low confidence |
| `S1-REDIRECT-CHAIN` | Two or more redirects in a row before a page resolves. A `<meta http-equiv="refresh">` with a delay of 0 or 1 counts as a hop. http mode only — fs mode never sees an HTTP redirect | medium — each hop costs crawl budget and a little link equity |
| `S1-REDIRECT-LOOP` | A URL redirects in a circle and never resolves | high |
| `S1-MIXED-CONTENT` | `http://` subresources on an https page (https is read from the page's canonical or the base origin) | high when a script, stylesheet or iframe is involved (browsers block them), medium for images and media |
| `S1-SECURITY-HEADERS` | `Strict-Transport-Security` or `X-Content-Type-Options` missing from responses. http mode on an https origin only, so a local dev server is never nagged | low |
| `S1-SITEMAP-LASTMOD-INVALID` | A `<lastmod>` that is not a W3C date (`2026-05-01`, `2026-05-01T10:00:00Z`) | medium — a malformed date is ignored |
| `S1-SITEMAP-LASTMOD-UNIFORM` | Five or more URLs, all with the same `<lastmod>` | low, medium confidence — crawlers learn to ignore a field that never varies |
| `S1-HREFLANG-INVALID` | `hreflang` is not a valid language code (`en_GB` instead of `en-GB`) | medium |
| `S1-HREFLANG-BROKEN` | An alternate names a page that does not exist. Alternates on another origin cannot be checked from one crawl and are skipped | high |
| `S1-HREFLANG-NO-SELF` | The page's hreflang set does not include the page itself | medium |
| `S1-HREFLANG-NO-RETURN` | Page A names B but B does not name A back (or has no hreflang at all); Google ignores a pair that is not reciprocal | high |
| `S1-JS-SHELL` | The source HTML is an empty client-rendered shell (empty `#root`/`#app`/`#__next` and almost no text). A guess from static HTML; `--render` proves it. While it stands, the page is not also reported as missing an H1 | medium, medium confidence |
| `S1-JS-DEPENDENT` | `--render` only: most of the page's text exists only after JavaScript runs (raw words vs rendered words in the evidence) | medium, medium confidence — Google renders eventually, most other crawlers do not |
| `S1-JS-HEAD-MISMATCH` | `--render` only: the canonical or the `noindex` directive differs between raw HTML and the rendered DOM | high — two different answers to "may this be indexed, and as what" |

Pagination (`rel=next/prev`) is deliberately not checked: Google stopped using it.

**`X-Robots-Tag: noindex`** is read from response headers in an http crawl and counts
as a `noindex` like the meta tag, so on the production homepage it is the critical
`S1-NOT-INDEXABLE` (or `S1-INDEX-CONTRADICTION` when the sitemap lists it). Preview
deploys send this header by design; it matters when the crawled URL is production.

**Meta-refresh stubs are redirects, not pages.** A page whose `<meta http-equiv="refresh">`
sends the visitor on within 0 or 1 seconds is never judged as a page, so it gets no title,
viewport, H1 or other on-page findings. An http crawl follows it like an HTTP redirect
(the stub's path becomes an alias of the target, and the refresh is one redirect hop); fs
mode records the stub as an alias of the file it points at. A stub that points off-site,
or at nothing on disk, is listed under skipped.

**Deliberate exclusions.** A `noindex` on a 404, or on a page also disallowed in
robots.txt, is a decision. Reported separately as a confirmation, never as a defect.
Utility pages matched: `404`, `403`, `500`, `offline`, `thank-you`.

**JS-rendered content.** An http crawl with `--render` parses Chrome's post-JavaScript DOM
(`chrome --headless --dump-dom`, no Playwright needed) for up to `--render-limit` pages
(default 50, about 5 seconds each) and keeps the raw HTML's head and word count so the
two can be compared. Status codes, redirects and headers still come from the plain
request. Without `--render`, `S1-JS-SHELL` is the only hint.

---

## Stage 2 — Architecture

| Finding | Means |
| --- | --- |
| `S2-ORPHAN` | Zero internal links point here. Google may never find it, and it receives no internal authority. |
| `S2-UNREACHABLE` | No path from the homepage by following links. Stronger than orphan. |
| `S2-LOW-INLINKS` | Exactly one inbound link. Weak signal for a page that matters. |
| `S2-DEEP` | More than 3 clicks from home. Depth correlates with reduced crawl frequency. |
| `S2-UNCRAWLABLE-LINK` | Navigation with no URL a crawler can follow: `<a href="#">`, `<a href="javascript:…">`, an `<a>` with no `href` but an `onclick` or `tabindex`, or a `<div>`/`<button>` whose `onclick` sets `location`. A bare `href="#"` with no `onclick` counts only in prose: a `<p>`, `<li>`, `<td>` or `<dd>` inside `<main>`/`<article>` (the whole body when there is neither), not inside a `<nav>`, `<figure>` or `<form>`, and not the `#` entry of a list whose other links are `#fragment`s (a table of contents). That keeps demo components and pagination out. Elements with `aria-hidden="true"` or `tabindex="-1"` are skipped. Evidence is the opening tag without `class`, plus the link text. Menu toggles (`role="button"`, `aria-expanded`, `aria-controls`, `aria-haspopup`, `data-toggle`), "back to top" links and a bare `<a>` with no handler (the current crumb in a breadcrumb) are not reported. Medium, medium confidence. |
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
| `S3-OG-MISSING` | Indexable page lacks `og:title`, `og:description` or `og:image` (names the missing ones). Low — sharing previews, not ranking. Noindex and 404-style pages are skipped. |

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
