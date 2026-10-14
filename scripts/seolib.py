"""Shared helpers for the sitemend skill: URL mapping, robots.txt, minhash, text.

Deterministic by design -- every hash here must produce identical output across
runs and machines so that `verify` can diff a later crawl against a baseline.
Python's builtin hash() is salted per-process and must never be used here.
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import urlsplit, urljoin

# Directories that must never be crawled. dist/build/_site are build output and are
# usually near-identical copies of the source -- crawling them turns every real page
# into a false duplicate-content finding, which is the highest-volume false positive
# this tool can produce.
EXCLUDE_DIRS = {
    "dist", "build", "_site", "out", "node_modules", ".git", ".next", ".nuxt",
    ".svelte-kit", ".cache", "coverage", "vendor", "__pycache__", ".venv",
    "venv", ".idea", ".vscode", "tmp", "temp",
    "sitemend-out",   # this tool's own default output, so a re-run never audits its last report
    "seo-repair-out", # the same, from before the rename to sitemend
}

HTML_EXT = (".html", ".htm")

MINHASH_N = 64
_MERSENNE = (1 << 61) - 1


def stable_hash64(s):
    """Deterministic 64-bit hash of a string."""
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def _perm_params(n=MINHASH_N):
    """Deterministic (a, b) multiplier/offset pairs for minhash permutations."""
    out = []
    for i in range(n):
        d = hashlib.blake2b(str(i).encode(), digest_size=16).digest()
        a = int.from_bytes(d[:8], "big") | 1
        b = int.from_bytes(d[8:], "big")
        out.append((a % _MERSENNE, b % _MERSENNE))
    return out


PERMS = _perm_params()


def shingles(text, k=8):
    """k-word shingles over normalized text."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def minhash(text, k=8):
    """MinHash signature -- lets audit.py estimate Jaccard similarity without
    storing every shingle of every page in crawl.json."""
    sh = shingles(text, k)
    if not sh:
        return None
    hs = [stable_hash64(s) % _MERSENNE for s in sh]
    return [min((a * h + b) % _MERSENNE for h in hs) for a, b in PERMS]


def minhash_similarity(sig_a, sig_b):
    """Estimated Jaccard similarity from two minhash signatures."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def claim_norm(s):
    """Collapse text to lowercase alphanumerics for claim corroboration.

    Structured data legitimately reformats what a page renders ("St" vs "Street",
    an address split across elements), so claims are matched against this squashed
    form rather than the raw text.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def normalize_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


def text_fingerprint(text):
    return hashlib.md5(normalize_text(text).lower().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- URL mapping

def file_to_urls(relpath):
    """Map a repo-relative HTML file path to (canonical_url, [aliases]).

    Handles BOTH conventions found on disk:
      about/index.html  -> '/about/'        (clean-URL directories)
      services.html     -> '/services.html' (flat files)

    Aliases capture the other forms a static host also serves, so a link written
    as '/services' resolves to services.html instead of reporting as broken.
    """
    p = relpath.replace("\\", "/").lstrip("./")
    parts = p.split("/")
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    parent = "/".join(parts[:-1])

    if stem == "index":
        url = "/" + (parent + "/" if parent else "")
        aliases = {url, url.rstrip("/") or "/", url + "index.html"}
    else:
        base = "/" + (parent + "/" if parent else "") + stem
        url = base + ".html"
        aliases = {url, base, base + "/"}
    return url, sorted(a for a in aliases if a)


def norm_path(path):
    """Normalize a URL path for lookup: strip query/fragment, collapse slashes."""
    if not path:
        return "/"
    path = path.split("#")[0].split("?")[0]
    path = re.sub(r"/{2,}", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return path


def canonical_http_path(path):
    """Collapse a URL's index-file form onto its directory form:
    '/index.html' -> '/', '/foo/index.html' -> '/foo/'.

    A server returns identical bytes for both, so fetching each as a separate page
    manufactures a false duplicate-content finding on nearly every static site --
    relative 'href="index.html"' links are completely ordinary. fs mode already
    collapses these via file_to_urls() aliases; http mode needs the same treatment.
    """
    p = norm_path(path)
    m = re.match(r"^(.*/)index\.html?$", p, re.I)
    return m.group(1) if m else p


def link_kind(href):
    """Classify a raw href before any resolution."""
    if href is None:
        return "empty"
    h = href.strip()
    if not h:
        return "empty"
    low = h.lower()
    if low.startswith("#"):
        return "anchor"
    for scheme in ("mailto:", "tel:", "javascript:", "sms:", "whatsapp:", "data:"):
        if low.startswith(scheme):
            return "nonhttp"
    return "link"


def resolve_href(page_url, href):
    """Resolve href relative to page URL. Returns (kind, resolved)."""
    kind = link_kind(href)
    if kind != "link":
        return kind, href
    if href.startswith("//"):
        return "absolute", "https:" + href
    parts = urlsplit(href)
    if parts.scheme in ("http", "https") and parts.netloc:
        return "absolute", href
    return "internal", norm_path(urljoin(page_url, href))


# ---------------------------------------------------------------- robots.txt

class Robots:
    """Minimal robots.txt model -- enough for indexability auditing, not a full RFC
    implementation. Records which rule matched so findings can cite real evidence."""

    def __init__(self, text=""):
        self.groups = []
        self.sitemaps = []
        self.raw = text or ""
        self.found = bool(text)
        self._parse(text)

    def _parse(self, text):
        cur = None
        last_was_agent = False
        for line in (text or "").splitlines():
            line = line.split("#")[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                if not last_was_agent or cur is None:
                    cur = {"agents": [], "rules": []}
                    self.groups.append(cur)
                cur["agents"].append(value.lower())
                last_was_agent = True
            elif field in ("allow", "disallow"):
                if cur is None:
                    cur = {"agents": ["*"], "rules": []}
                    self.groups.append(cur)
                cur["rules"].append((field == "allow", value))
                last_was_agent = False
            elif field == "sitemap":
                self.sitemaps.append(value)
                last_was_agent = False

    @staticmethod
    def _match(pattern, path):
        if pattern == "":
            return None
        anchored_end = pattern.endswith("$")
        pat = pattern[:-1] if anchored_end else pattern
        rx = "".join(".*" if c == "*" else re.escape(c) for c in pat)
        rx = "^" + rx + ("$" if anchored_end else "")
        return len(pat.replace("*", "")) if re.match(rx, path) else None

    def check(self, path, agent="*"):
        """Return (allowed, matched_rule). Longest matching rule wins."""
        agent = agent.lower()
        group = None
        for g in self.groups:
            if agent in g["agents"]:
                group = g
                break
        if group is None:
            for g in self.groups:
                if "*" in g["agents"]:
                    group = g
                    break
        if group is None:
            return True, None
        best = None
        for allow, pattern in group["rules"]:
            ln = self._match(pattern, path)
            if ln is None:
                continue
            if best is None or ln > best[0] or (ln == best[0] and allow):
                best = (ln, allow, pattern)
        if best is None:
            return True, None
        return best[1], ("Allow: " if best[1] else "Disallow: ") + best[2]


def parse_sitemap(xml_text):
    """Extract <loc> values. Handles urlset and sitemapindex alike."""
    if not xml_text:
        return [], ["not found"]
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, re.I | re.S)
    errs = []
    if "<sitemapindex" in xml_text.lower():
        errs.append("sitemapindex")
    if not locs:
        errs.append("no <loc> entries found")
    return [l.strip() for l in locs], errs


def parse_sitemap_lastmods(xml_text):
    """[(loc, lastmod_or_None)] for every <url> block. Kept apart from parse_sitemap so
    the many callers of that function keep their shape."""
    out = []
    for block in re.findall(r"<url>(.*?)</url>", xml_text or "", re.I | re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.I | re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.I | re.S)
        if loc:
            out.append((loc.group(1).strip(), lm.group(1).strip() if lm else None))
    return out


# ------------------------------------------------------------ rendered DOM (Chrome)

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "chrome", "msedge", "microsoft-edge")
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def find_chrome():
    """A Chrome/Chromium/Edge binary, or None. CHROME_PATH wins (Lighthouse honours it too)."""
    env = os.environ.get("CHROME_PATH")
    if env and os.path.isfile(env):
        return env
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in CHROME_PATHS if os.path.isfile(p)), None)


def chrome_cmd(chrome, url, profile, nt=None, root=None, env=None):
    """The headless-Chrome command line. --no-sandbox runs the audited site's JavaScript
    without Chrome's sandbox, so it is added only for root (Chrome refuses to start as
    root otherwise) or when SITEMEND_NO_SANDBOX=1 (containers, some CI images).
    nt/root/env are parameters so tests need no particular OS."""
    nt = os.name == "nt" if nt is None else nt
    env = os.environ if env is None else env
    root = (not nt and os.geteuid() == 0) if root is None else root
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
           "--user-data-dir=" + profile, "--virtual-time-budget=8000", "--dump-dom", url]
    if not nt and (root or env.get("SITEMEND_NO_SANDBOX") == "1"):
        cmd.insert(1, "--no-sandbox")
    return cmd


def render_dom(chrome, url, timeout=30):
    """The DOM after JavaScript has run, via `chrome --headless --dump-dom`.

    No Playwright or Selenium: the Lighthouse step already needs a local Chrome, and
    --dump-dom is the platform's own answer to "what does the page look like after JS".
    Returns (html_or_None, error_or_None). The profile is throwaway; on Windows Chrome
    often holds it open past exit, so cleanup failures are ignored.
    """
    profile = tempfile.mkdtemp(prefix="sitemend-chrome-")
    nt = os.name == "nt"
    cmd = chrome_cmd(chrome, url, profile)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=not nt)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if nt:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return None, "render timed out after {0}s".format(timeout)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    html = out.decode("utf-8", "replace")
    if "<html" not in html.lower():
        return None, "chrome returned no document"
    return html, None


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
