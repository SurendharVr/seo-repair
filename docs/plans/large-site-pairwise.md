# Large sites: exact candidate filters for the pairwise content checks

**Status:** plan, not implemented. Measured on 2026-10-16 against `main` at 8eb58a5 (1.4.3).

**Goal:** `S3-NEAR-DUPLICATE` and `S3-CANNIBALIZATION` stop comparing every pair of live
pages, with **byte-identical `audit()` output**: no pair found today is missed and no new
pair appears.

**Approach:** both checks keep their exact comparison and threshold. Only the list of
pairs they compare changes, and each filter is *provably* lossless at the current
threshold, not probabilistically so:

- **Near-duplicates: LSH banding with a pigeonhole guarantee.** The 64 MinHash values are
  cut into 16 bands of 4. A pair at `NEAR_DUP = 0.80` agrees on at least 52 of 64 values
  (51/64 = 0.797), so it differs in at most 12. Twelve differences cannot touch all 16
  bands, so at least one band is identical and the pair lands in a shared bucket. Every
  pair at or above the threshold becomes a candidate. Candidates are then scored with the
  same `minhash_similarity` as today.
- **Cannibalization: prefix filtering over an inverted index.** For term sets with Jaccard
  `>= t`, `|A ∩ B| >= ceil(t * max(|A|, |B|))`. If the terms of every set are sorted in one
  global order (rarest first), two such sets must share a term within the first
  `|A| - ceil(t * |A|) + 1` terms of each. Index only that prefix. With 12 terms and
  `t = 0.60` that is the 5 rarest terms of each page. Candidates are then scored with the
  same Jaccard as today.

A plain "shares at least one term" inverted index was also measured and **rejected**: on
a templated site it produced more candidates than it saved, and ran slower than the
exhaustive loop (see Measurements).

**Also in this plan (Task 5):** `group_findings` checks page membership in a list, which
is quadratic when one finding covers the whole site (no breadcrumbs, no Open Graph tags).
Once the pairwise checks are fast, it is half of what's left: 9.3 s of a 10,000-page audit.

**Tech stack:** Python 3.9+ standard library (`fractions`, `math`, `collections`). No new
dependencies. No change to `crawl.py`, `seolib.py`, thresholds or `crawl.json`.

---

## Measurements

All numbers are wall-clock on one machine (Windows 11, 4 cores, Python 3.12.10), one run
each, measured in-process around `audit.audit()`. Nothing below is extrapolated.

### Generated fs sites

Each page has a random 400-word body (Zipf-weighted pseudo-word vocabulary of ~8,000
words), a 5-word title, a 4-word H1 and a 14-word description. Planted per site: `n/100`
(minimum 10) near-duplicate pairs made by copying a body and replacing 1 to 8 words
(true 8-shingle Jaccard 0.94 down to 0.71, so they straddle 0.80), and `n/100`
cannibalizing pairs that share title, H1 and description (half with one title word
changed).

| Pages | All pairs | `main`: pairwise | `main`: audit | Filtered: pairwise | Filtered: audit | + Task 5 | Near-dup candidates | Cannibal candidates |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 500 | 124,750 | 1.2 s | 1.3 s | 0.05 s | 0.21 s | 0.22 s | 10 | 574 |
| 2,000 | 1,999,000 | 19.8 s | 20.7 s | 0.26 s | 1.1 s | 1.1 s | 20 | 12,156 |
| 5,000 | 12,497,500 | 137.4 s | 141.3 s | 1.2 s | 5.1 s | 3.3 s | 50 | 80,366 |
| 10,000 | 49,995,000 | 604.4 s | 620.8 s | 4.0 s | 17.9 s | 9.3 s | 100 | 326,120 |

"Pairwise" is the time inside the two checks; "audit" is the whole `audit()` call.
"Filtered" is the prototype of Tasks 2 and 4; "+ Task 5" is the whole audit with this
plan's exact code for Tasks 2, 4 and 5 (a separate run). "Candidates" is how many pairs
each filter hands to the exact comparison.

At 10,000 pages `main` spends 425.6 s on near-duplicates and 178.8 s on cannibalization.
The rejected shared-term index gave 43,923 / 720,520 / 4,527,979 / 18,040,828 cannibal
candidates at the four sizes, and at 10,000 pages took **202.9 s, slower than `main`'s
178.8 s**: looking up and deduplicating 18 million candidate pairs costs more than
comparing 50 million pairs of 12-term sets.

For context, crawling these sites (`crawl.py`, fs mode) took about 30 s, 111 s, 297 s and
441 s: linear, 45 to 60 ms a page.

**Correctness, at every size:** the complete `audit()` result (every finding in order,
plus gates and exclusions, compared as JSON) is identical to `main`'s, for the
prototype and for the exact code in Tasks 2, 4 and 5 below. Missed pairs: 0. Added
pairs: 0. Near-duplicate findings: 8, 13, 34, 57 at the four sizes, the same pairs on both
sides. Cannibalization findings: 10, 20, 50, 100: every planted pair, on both sides.
No unplanted pair was reported by either.

The planted near-duplicates that neither side reports are the ones whose MinHash
estimate falls under 0.80. That is the 64-value estimate's own noise, on `main` today
and unchanged by this plan:

| Words replaced | True Jaccard | Reported (all four sites) |
|---:|---|---:|
| 1 | 0.94 to 0.97 | 25 of 25 |
| 2 | 0.90 to 0.92 | 25 of 25 |
| 3 | 0.86 to 0.89 | 20 of 23 |
| 4 | 0.83 to 0.86 | 16 of 23 |
| 5 | 0.80 to 0.83 | 11 of 21 |
| 6 | 0.77 to 0.81 | 8 of 21 |
| 7 | 0.74 to 0.77 | 5 of 21 |
| 8 | 0.71 to 0.75 | 2 of 21 |

See "Not in this plan".

### Adversarial inputs (signatures and term sets built directly)

- **Near-duplicates:** 3,000 signatures in 60 clusters, each a shared base with 0 to 30
  of 64 values replaced, so thousands of pairs sit on each side of 0.80. Exhaustive:
  7,264 pairs at or above 0.80, in 32.9 s. Banding: 54,928 candidates (1.2% of
  4,498,500 pairs), the same 7,264 pairs, **0 missed, 0 added**, in 0.7 s.
- **Cannibalization:** 4,000 term sets of 12. Four "section" words each on a quarter of
  the pages (under the 35% template-word cut, so they stay in), 100 clusters of 12 sets
  sharing 6 to 11 core terms, Zipf filler. Exhaustive: 2,025 pairs at or above 0.60, in
  32.7 s. Prefix filter: 91,881 candidates (1.1% of 7,998,000), the same 2,025 pairs,
  **0 missed, 0 added**, in 1.7 s. Shared-term index: the same 2,025 pairs from 5,464,631
  candidates (68%), in 58.2 s.

### Demo sites and test suite

- `examples/demo-site` and `examples/demo-site-intl`, full RELEASING.md §2 pipeline with
  this plan's code: `24 findings: 1 critical, 7 high, 7 medium, 9 low` and
  `11 findings: 0 critical, 3 high, 7 medium, 1 low`, the same as `main`. `findings.json`
  differs only in `meta.source` and `meta.crawled_at`. Neither site has a near-duplicate
  or cannibalization finding, so this shows nothing new appears.
- `python tests/test_skill.py` with the prototype in place of `scripts/audit.py`:
  **215 tests, OK (skipped=2)**, the same as `main`. With this plan's code and tests
  (Tasks 1 to 5): **223 tests, OK (skipped=2)**. That includes
  `test_cannibalization_suppressed_when_duplicate`,
  `test_templated_siblings_do_not_cannibalize`,
  `test_real_cannibal_pair_still_flagged_among_templated_siblings` and the hreflang
  alternates test.

### The 20-site benchmark

[`docs/benchmark-2026-10.md`](../benchmark-2026-10.md) can't be re-run from the repo: it
names no sites, by design. It also doesn't bear on this change. It crawled 50 pages per
site, and it lists `S3-NEAR-DUPLICATE` and `S3-CANNIBALIZATION` under **not measured**,
because a partial crawl can't say whether two pages compete. The demo sites, the test
suite and the generated sites above are the evidence instead.

### Who hits this

An http crawl stops at `--max-pages` (200 by default through `sitemend audit`), so only
fs mode reaches thousands of pages. A docs site built from a repo can, especially with
versioned docs. The README already says "built for sites up to a few thousand pages"
because of these two loops.

---

## Task 1: A failing test that pins the near-duplicate candidate guarantee

**Files:**
- Modify: `tests/test_skill.py` (new class after `TestGrouping`)

- [ ] **Step 1: Write the failing test**

```python
class TestPairwiseCandidates(unittest.TestCase):
    """The near-duplicate and cannibalization checks compare only candidate pairs. The
    filters must be lossless at the current thresholds: a pair the exhaustive loop would
    report and the filter drops is a silent false negative, the worst kind for a tool
    whose findings have to be trusted."""

    def _sig_pages(self, seed=11):
        import random
        rnd = random.Random(seed)
        pages = []
        for _ in range(12):                       # 12 clusters of 25 around a base
            base = [rnd.getrandbits(61) for _ in range(S.MINHASH_N)]
            for _ in range(25):
                sig = list(base)
                for pos in rnd.sample(range(S.MINHASH_N), rnd.randint(0, 30)):
                    sig[pos] = rnd.getrandbits(61)
                pages.append({"minhash": sig, "word_count": 400})
        return pages

    def test_near_duplicate_candidates_include_every_pair_at_the_threshold(self):
        pages = self._sig_pages()
        cand = set(audit.near_dup_candidates(pages))
        hits = 0
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                sim = S.minhash_similarity(pages[i]["minhash"], pages[j]["minhash"])
                if sim >= audit.NEAR_DUP:
                    hits += 1
                    self.assertIn((i, j), cand, "pair at {0:.3f} not compared".format(sim))
        self.assertGreater(hits, 100, "fixture must put pairs on both sides of 0.80")

    def test_worst_case_near_duplicate_is_a_candidate(self):
        """12 differing values, one in each of the first 12 bands: the most a pair at
        0.80 can differ. Only the last four bands still match."""
        a = list(range(S.MINHASH_N))
        b = list(a)
        r = S.MINHASH_N // audit.BANDS
        for band in range(12):
            b[band * r] = -1
        self.assertGreaterEqual(S.minhash_similarity(a, b), audit.NEAR_DUP)
        pages = [{"minhash": a, "word_count": 400}, {"minhash": b, "word_count": 400}]
        self.assertEqual(audit.near_dup_candidates(pages), [(0, 1)])

    def test_unrelated_pages_are_not_candidates(self):
        """The point of the filter: 300 unrelated pages give no candidates."""
        import random
        rnd = random.Random(5)
        pages = [{"minhash": [rnd.getrandbits(61) for _ in range(S.MINHASH_N)],
                  "word_count": 400} for _ in range(300)]
        self.assertEqual(audit.near_dup_candidates(pages), [])

    def test_short_pages_are_not_candidates(self):
        sig = list(range(S.MINHASH_N))
        pages = [{"minhash": sig, "word_count": 400}, {"minhash": sig, "word_count": 49}]
        self.assertEqual(audit.near_dup_candidates(pages), [])
```

- [ ] **Step 2: Run it and confirm it fails for the right reason**

```bash
python -m unittest tests.test_skill.TestPairwiseCandidates -v
```

Expected: 4 errors, `AttributeError: module 'audit' has no attribute 'near_dup_candidates'`
(`'BANDS'` for the worst-case test).

## Task 2: Near-duplicate candidates by banding

**Files:**
- Modify: `scripts/audit.py` (imports, constants, one helper, the near-duplicate loop)

- [ ] **Step 1: Imports and constants**

Add `import math` and `from fractions import Fraction` to the imports. Under
`NEAR_DUP = 0.80`:

```python
# Near-duplicate candidates: the 64 minhash values in BANDS bands. A pair at NEAR_DUP
# differs in at most MINHASH_N - ceil(NEAR_DUP * MINHASH_N) values (12), and that many
# differences cannot break more than 12 bands, so with 16 one band always matches and
# every pair at the threshold is compared. NEAR_DUP at 0.75 or lower breaks this:
# the assert below stops the import rather than let pairs go silently unchecked.
BANDS = 16
assert S.MINHASH_N % BANDS == 0 and \
    BANDS > S.MINHASH_N - math.ceil(Fraction(str(NEAR_DUP)) * S.MINHASH_N)
```

`Fraction(str(...))` keeps the bound exact: `0.80 * 64` in floats is `51.2`, but a
threshold like `0.6 * 5` must not round up past an integer.

- [ ] **Step 2: The helpers** (after `top_terms`)

```python
def _bucket_pairs(buckets):
    """Index pairs (i < j) that share any bucket, sorted so findings keep the order
    of the exhaustive loop."""
    pairs = set()
    for idx in buckets.values():
        for x, i in enumerate(idx):
            for j in idx[x + 1:]:
                pairs.add((i, j))
    return sorted(pairs)


def near_dup_candidates(pages):
    """Pairs of pages that share at least one whole band of minhash values. Every pair
    at or above NEAR_DUP is among them (see BANDS)."""
    r = S.MINHASH_N // BANDS
    buckets = defaultdict(list)
    for i, p in enumerate(pages):
        sig = p.get("minhash")
        if p["word_count"] < 50 or not sig or len(sig) != S.MINHASH_N:
            continue
        for band in range(BANDS):
            buckets[(band, tuple(sig[band * r:(band + 1) * r]))].append(i)
    return _bucket_pairs(buckets)
```

A page skipped here is one the exhaustive loop also never reports: under 50 words is
skipped there, and `minhash_similarity` returns 0 for a missing signature or a length
mismatch. `MINHASH_N` has been 64 since v1.0.0, so a stored signature of another length
doesn't exist.

- [ ] **Step 3: Use it in the loop.** Replace

```python
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if frozenset((a["url"], b["url"])) in seen_pairs:
                continue
            if a["word_count"] < 50 or b["word_count"] < 50:
                continue
            sim = S.minhash_similarity(a.get("minhash"), b.get("minhash"))
            if sim >= NEAR_DUP:
```

with

```python
    for i, j in near_dup_candidates(live):
        a, b = live[i], live[j]
        if frozenset((a["url"], b["url"])) in seen_pairs:
            continue
        sim = S.minhash_similarity(a.get("minhash"), b.get("minhash"))
        if sim >= NEAR_DUP:
```

and dedent the body of the `if` (the `seen_pairs.add` and `out.append`) by one level.
The `word_count < 50` test moves into `near_dup_candidates`.

- [ ] **Step 4: Run the new tests and the suite**

```bash
python -m unittest tests.test_skill.TestPairwiseCandidates -v
python tests/test_skill.py
```

Expected: 4 passed; then `OK (skipped=2)` with the count up by 4.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit.py tests/test_skill.py
git commit -m "perf: near-duplicate check compares only pairs that share a minhash band"
```

## Task 3: A failing test for the cannibalization prefix filter

**Files:**
- Modify: `tests/test_skill.py` (same class)

- [ ] **Step 1: Write the failing tests**

```python
    def _jaccard(self, a, b):
        return len(a & b) / len(a | b)

    def _term_sets(self, seed=3, n=600):
        """Template words on a quarter of the pages each (under the 35% boiler cut),
        clusters sharing 6 to 11 of 12 terms, Zipf filler."""
        import random
        rnd = random.Random(seed)
        vocab = ["t%d" % i for i in range(800)]
        weights = [1 / (i + 1) for i in range(800)]
        sigs, urls = {}, []
        for i in range(n):
            s = {"section%d" % (i % 4)}
            if i < 240:
                core = random.Random(i // 12).sample(vocab[100:], 12)
                s |= set(core[:rnd.randint(6, 11)])
            while len(s) < 12:
                s.add(rnd.choices(vocab, weights)[0])
            urls.append("/%d" % i)
            sigs[urls[-1]] = s
        return urls, sigs

    def test_cannibal_candidates_include_every_pair_at_the_threshold(self):
        urls, sigs = self._term_sets()
        cand = set(audit.cannibal_candidates(urls, sigs))
        hits = 0
        for i in range(len(urls)):
            for j in range(i + 1, len(urls)):
                if self._jaccard(sigs[urls[i]], sigs[urls[j]]) >= audit.CANNIBAL_TERMS:
                    hits += 1
                    self.assertIn((i, j), cand)
        self.assertGreater(hits, 100, "fixture must put pairs on both sides of 0.60")

    def test_cannibal_candidates_handle_short_and_empty_sets(self):
        """After template words are removed a page can keep 1 or 0 terms. One shared
        term out of one is Jaccard 1.0 and must be compared; an empty set never is."""
        urls = ["/a", "/b", "/c"]
        sigs = {"/a": {"widget"}, "/b": {"widget"}, "/c": set()}
        self.assertEqual(audit.cannibal_candidates(urls, sigs), [(0, 1)])

    def test_templated_terms_do_not_make_every_pair_a_candidate(self):
        """Guard against the rejected design: indexing every term makes 71% of these
        pairs candidates (prefix filtering: 4%), and on a 4,000-page templated set it
        ran slower than comparing every pair."""
        urls, sigs = self._term_sets()
        total = len(urls) * (len(urls) - 1) // 2
        self.assertLess(len(audit.cannibal_candidates(urls, sigs)), total // 10)
```

- [ ] **Step 2: Run and confirm the failure**

```bash
python -m unittest tests.test_skill.TestPairwiseCandidates -v
```

Expected: the 3 new tests error with `AttributeError: ... 'cannibal_candidates'`; the
4 from Task 1 still pass.

## Task 4: Cannibalization candidates by prefix filtering

**Files:**
- Modify: `scripts/audit.py` (one helper, the cannibalization loop)

- [ ] **Step 1: The helper** (after `near_dup_candidates`)

```python
def cannibal_candidates(urls, sigs):
    """Pairs whose term sets could reach CANNIBAL_TERMS Jaccard. Jaccard >= t needs
    |A & B| >= ceil(t * |A|), so with every set sorted rarest term first, two such sets
    share a term among the first |A| - ceil(t * |A|) + 1 of each (prefix filtering).
    Only those terms are indexed; common terms, which would pair up most of a
    templated site, sort last and drop out."""
    t = Fraction(str(CANNIBAL_TERMS))
    df = defaultdict(int)
    for u in urls:
        for w in sigs[u]:
            df[w] += 1
    buckets = defaultdict(list)
    for i, u in enumerate(urls):
        terms = sorted(sigs[u], key=lambda w: (df[w], w))
        for w in terms[:len(terms) - math.ceil(t * len(terms)) + 1]:
            buckets[w].append(i)
    return _bucket_pairs(buckets)
```

An empty set indexes nothing, which matches the exhaustive loop's `if not sigs[a] or not
sigs[b]: continue`.

- [ ] **Step 2: Use it in the loop.** Replace

```python
    for i, a in enumerate(urls):
        for b in urls[i + 1:]:
            if frozenset((a, b)) in seen_pairs:
                continue          # already reported as a duplicate; same root cause
            if not sigs[a] or not sigs[b]:
                continue
```

with

```python
    for ia, ib in cannibal_candidates(urls, sigs):
        a, b = urls[ia], urls[ib]
        if frozenset((a, b)) in seen_pairs:
            continue          # already reported as a duplicate; same root cause
```

and dedent the rest of the loop body by one level. The indices are `ia, ib`, not
`i, j`: the body already uses `j` for the Jaccard score.

- [ ] **Step 3: Run the tests and the suite**

```bash
python -m unittest tests.test_skill.TestPairwiseCandidates -v
python tests/test_skill.py
```

Expected: 7 passed; then `OK (skipped=2)`, 7 more tests than before Task 1.

- [ ] **Step 4: Commit**

```bash
git add scripts/audit.py tests/test_skill.py
git commit -m "perf: cannibalization check compares only pairs that share a rare term"
```

## Task 5: Group site-wide findings in linear time

**Files:**
- Modify: `tests/test_skill.py` (`import time` at the top; one test in `TestGrouping`)
- Modify: `scripts/audit.py` (`group_findings`)

- [ ] **Step 1: Write the failing test** (in `TestGrouping`)

```python
    def test_grouping_a_site_wide_finding_is_linear(self):
        """A finding with the same evidence on every page (no breadcrumbs, no Open Graph
        tags) groups into one. Checking membership in the page list made that quadratic:
        17 s for 40,000 pages, and 9 s of a 10,000-page audit."""
        fs = [{"id": "S2-BREADCRUMB-MISSING", "pages": ["/p%d" % i],
               "evidence": "jsonld types: none", "severity": "low", "stage": 2,
               "message": "m"} for i in range(40000)]
        fs.append(dict(fs[0]))                     # the same page twice counts once
        t = time.perf_counter()
        grouped = audit.group_findings(fs)
        self.assertLess(time.perf_counter() - t, 3.0)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["affects"], 40000)
        self.assertEqual(len(grouped[0]["pages"]), 40000)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m unittest tests.test_skill.TestGrouping.test_grouping_a_site_wide_finding_is_linear -v
```

Expected: `AssertionError: 16.48... not less than 3.0` (16 to 17 s measured on `main`; the
fixed code takes 0.04 s, so the 3 s bound leaves room for a slow CI runner).

- [ ] **Step 3: Fix it.** In `group_findings`, keep a set per group beside the list:

```python
    grouped, order, members = {}, [], {}
```

```python
        if key in grouped:
            g = grouped[key]
            for u in f["pages"]:
                if u not in members[key]:
                    members[key].add(u)
                    g["pages"].append(u)
        else:
            g = dict(f, pages=list(f["pages"]))
            grouped[key] = g
            members[key] = set(g["pages"])
            order.append(key)
```

The page lists are sorted at the end as before, so the output doesn't change.

- [ ] **Step 4: Run the test and the suite**

```bash
python -m unittest tests.test_skill.TestGrouping -v
python tests/test_skill.py
```

Expected: `TestGrouping` passes; then `OK (skipped=2)`.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit.py tests/test_skill.py
git commit -m "perf: group site-wide findings without a list scan per page"
```

## Task 6: Prove the output didn't change

- [ ] **Step 1: Demo sites (RELEASING.md §2).** Run on this branch and on `main`, and diff:

```bash
cd scripts
python crawl.py        --root ../examples/demo-site --out /tmp/crawl.json
python schema_check.py --crawl /tmp/crawl.json      --out /tmp/schema.json
python audit.py        --crawl /tmp/crawl.json --schema /tmp/schema.json --out /tmp/findings.json
```

Expected: `24 findings: 1 critical, 7 high, 7 medium, 9 low`, the same gate lines as
`main`, and `findings.json` identical except `meta.source` and `meta.crawled_at`.
`EXPECTED.md` and the CI `required` set don't change. Same for `examples/demo-site-intl`
against its `EXPECTED.md`. (Checked with this plan's code: both demo sites diff clean
apart from those two lines.)

- [ ] **Step 2: Large generated sites.** In a throwaway folder outside the repo, generate
the 2,000 and 10,000-page sites with the generator in the appendix, crawl each once, and
run `audit.py` from `main` and from this branch on the same `crawl.json`:

```bash
python gen.py 10000 s10000
python <repo>/scripts/crawl.py --root s10000/site --base-url https://bench.test --out s10000/crawl.json
python <main-checkout>/scripts/audit.py --crawl s10000/crawl.json --out main.json
python <repo>/scripts/audit.py          --crawl s10000/crawl.json --out branch.json
python -c "import json; a, b = (json.load(open(f)) for f in ('main.json', 'branch.json')); print(a['findings'] == b['findings'] and a['gates'] == b['gates'])"
```

Expected: `True`. `main` takes about 10 minutes on the 10,000-page site; the branch takes
about 9 seconds (see Measurements).

## Task 7: Docs and changelog

- [ ] **Step 1:** README "Known limits": replace the "Built for sites up to a few
thousand pages" bullet with:

```markdown
- **An http crawl stops at `--max-pages`.** fs mode has no page cap. The duplicate and
  cannibalization checks compare only pairs that could match, so once a 10,000-page
  folder is crawled (about 7 minutes) the audit takes about 9 seconds.
```

- [ ] **Step 2:** `CHANGELOG.md` under `## Unreleased`:

```markdown
- **Large sites audit in seconds, with the same findings.** The near-duplicate and
  cannibalization checks no longer compare every pair of pages. They compare only pairs
  that could reach the threshold, chosen by filters that can't drop a qualifying pair
  (MinHash banding and prefix filtering). On a generated 10,000-page site the audit went
  from 621 s to 9 s with identical output.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: large-site audit times"
```

## Not in this plan

- **Lowering `NEAR_DUP` to 0.75 or below.** The banding guarantee needs more than
  `64 * (1 - NEAR_DUP)` bands; at 0.75 that is 16, so the import-time assert fails and
  `BANDS` must become 32 (bands of 2), with more candidates. The assert makes this a
  loud failure, not a silent recall loss.
- **MinHash's own estimation noise.** With 64 values the estimate's standard deviation
  near 0.80 is about 0.05, so pairs just above 0.80 are often missed (11 of 21 planted
  pairs at true 0.80 to 0.83 were reported) and pairs a little below are sometimes
  reported (8 of 21 at true 0.77 to 0.81, most of them under 0.80). That is `main`'s recall today, and this plan keeps it exactly. Tightening it
  means more permutations (bigger `crawl.json`, new baselines, more band arithmetic) or
  re-checking candidates on stored shingles. Separate decision, with its own benchmark.
- **`crawl.py` speed.** The fs crawl took 45 to 60 ms a page (441 s for the 10,000-page
  site). It now dominates a large-site run, but it's linear, not quadratic.

---

## Appendix: the benchmark generator

The throwaway scripts are not committed. The generator is reproduced here so the
numbers can be checked:

```python
"""Generate an fs site of N pages: random 400-word bodies + planted near-dup and
cannibalizing pairs. Writes <out>/site/ and <out>/planted.json."""
import json, os, random, sys

def main(n, out, seed=1):
    rnd = random.Random(seed)
    syl = ["ka","ro","mi","te","lu","sa","ven","dor","pli","qua","zen","bri","to","nax","el","fu","gro","shi"]
    vocab = sorted({"".join(rnd.choice(syl) for _ in range(rnd.randint(2, 4))) for _ in range(8000)})
    weights = [1 / (i + 1) ** 0.9 for i in range(len(vocab))]   # Zipf-ish
    def words(k):
        return rnd.choices(vocab, weights, k=k)
    pages = []
    for i in range(n):
        pages.append({"slug": "p%05d" % i, "title": " ".join(words(5)), "h1": " ".join(words(4)),
                      "desc": " ".join(words(14)), "body": words(400)})
    planted = {"near_dup": [], "cannibal": []}
    nd, cb = max(10, n // 100), max(10, n // 100)
    idx = rnd.sample(range(1, n), 2 * (nd + cb))
    for j in range(nd):                      # copy body, replace k words (k=1..8)
        a, b = idx[2 * j], idx[2 * j + 1]
        k = 1 + j % 8
        body = list(pages[a]["body"])
        for pos in rnd.sample(range(400), k):
            body[pos] = rnd.choice(vocab)
        pages[b]["body"] = body
        planted["near_dup"].append([pages[a]["slug"], pages[b]["slug"], k])
    off = 2 * nd
    for j in range(cb):                      # same head terms, different body
        a, b = idx[off + 2 * j], idx[off + 2 * j + 1]
        for f in ("title", "h1", "desc"):
            pages[b][f] = pages[a][f]
        if j % 2:                            # partial overlap: swap one title word
            t = pages[b]["title"].split(); t[0] = rnd.choice(vocab); pages[b]["title"] = " ".join(t)
        planted["cannibal"].append([pages[a]["slug"], pages[b]["slug"]])
    site = os.path.join(out, "site")
    os.makedirs(site, exist_ok=True)
    for i, p in enumerate(pages):
        name = "index.html" if i == 0 else p["slug"] + ".html"
        nav = "".join('<a href="/%s.html">%s</a> ' % (pages[x]["slug"], pages[x]["h1"])
                      for x in {(i + 1) % n or 1, (i * 7 + 3) % n or 1, (i * 13 + 5) % n or 1})
        html = ("<!doctype html><html lang=en><head><meta charset=utf-8><title>{t}</title>"
                "<meta name=description content=\"{d}\"><link rel=canonical href=\"https://bench.test/{u}\">"
                "</head><body><nav><a href=\"/\">home</a> {nav}</nav><main><h1>{h}</h1><p>{b}</p></main></body></html>"
                ).format(t=p["title"], d=p["desc"], h=p["h1"], b=" ".join(p["body"]), nav=nav,
                         u="" if i == 0 else p["slug"] + ".html")
        with open(os.path.join(site, name), "w", encoding="utf-8") as f:
            f.write(html)
    json.dump(planted, open(os.path.join(out, "planted.json"), "w"), indent=1)

if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2])
```
