"""
keywords.py
-----------
Free keyword discovery. No paid SEO tool, so there is NO search volume and NO
difficulty score: those columns are always "Not verified".

  * Google autocomplete (public suggest endpoint): phrases people really type.
    Expansion = seed + question prefixes + connector suffixes + A-Z, then (depth 2)
    the best finds are expanded once more.
  * Google Trends via pytrends (unofficial, often blocked from cloud IPs): relative
    interest by calendar month. If it fails the caller falls back to the months you
    declared in config.yaml and labels them "user_declared".
"""
from __future__ import annotations

import json
import re
import string
import time
from typing import Optional

import requests

AMAZON_US_MID = "ATVPDKIKX0DER"          # amazon.com marketplace id
PREFIXES = ["best", "how to", "why", "is", "what", "can", "does", "should"]
SUFFIXES = ["for", "vs", "without", "with", "that", "under"]
DEFAULT_SOURCES = ["google", "bing", "duckduckgo", "amazon"]
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
BREAKER = 4          # consecutive failed requests before a source is given up for this run

# Words that say WHAT the searcher wants (intent) or glue a phrase together, not WHAT it is about.
INTENT_WORDS = set("""
best top review reviews vs versus compare comparison cheap cheapest buy price prices deal deals
how to why what can does do is are should a an the of in on for and or with without that your you my
under amazon near me which who where when it this these those
""".split())
STOP = INTENT_WORDS


def _request(source: str, q: str) -> tuple:
    """(url, params) for one suggestion request."""
    if source == "google":
        return "https://suggestqueries.google.com/complete/search", {"client": "chrome", "q": q, "hl": "en", "gl": "us"}
    if source == "bing":
        return "https://api.bing.com/osjson.aspx", {"query": q, "mkt": "en-US"}
    if source == "duckduckgo":
        return "https://duckduckgo.com/ac/", {"q": q, "type": "list"}
    if source == "amazon":
        return "https://completion.amazon.com/api/2017/suggestions", {"mid": AMAZON_US_MID, "alias": "aps", "prefix": q,
                                                                      "limit": 11, "suggestion-type": "KEYWORD"}
    raise ValueError(f"unknown suggestion source: {source}")


def parse_suggestions(data) -> list:
    """Understands every shape these endpoints return:
    ["query", ["s1", "s2"], ...]  (Google, Bing, DuckDuckGo type=list)
    [{"phrase": "s1"}, ...]       (DuckDuckGo default)
    {"suggestions": [{"value": "s1"}, ...]}  (Amazon)"""
    out = []
    if isinstance(data, dict):
        for item in data.get("suggestions") or []:
            v = item.get("value") if isinstance(item, dict) else item
            if isinstance(v, str):
                out.append(v)
    elif isinstance(data, list):
        if len(data) >= 2 and isinstance(data[1], list):
            out = [x for x in data[1] if isinstance(x, str)]
        else:
            for item in data:
                if isinstance(item, dict):
                    v = item.get("phrase") or item.get("value")
                    if isinstance(v, str):
                        out.append(v)
    return out


class Stats:
    """Per-source counters so a failed run can say exactly why."""

    def __init__(self, sources):
        self.s = {src: {"requests": 0, "with_results": 0, "empty": 0, "errors": 0, "streak": 0, "last_error": "", "disabled": False}
                  for src in sources}

    def to_dict(self) -> dict:
        return {k: {kk: vv for kk, vv in v.items() if kk != "streak"} for k, v in self.s.items()}

    def lines(self) -> list:
        out = []
        for k, v in self.s.items():
            tail = f", last error: {v['last_error']}" if v["last_error"] else ""
            off = " (given up after repeated failures)" if v["disabled"] else ""
            out.append(f"{k}: {v['requests']} requests, {v['with_results']} returned suggestions, {v['empty']} empty, {v['errors']} errors{tail}{off}")
        return out


def autocomplete(term: str, source: str = "google", stats: Optional[Stats] = None, timeout: int = 10) -> list:
    """Suggestions from one engine. Never raises; failures are counted in `stats` with the reason."""
    st = stats.s[source] if stats is not None and source in stats.s else None
    if st is not None:
        if st["disabled"]:
            return []
        st["requests"] += 1
    try:
        url, params = _request(source, term)
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": BROWSER_UA, "Accept": "application/json, text/javascript, */*",
                                  "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        text = r.content.decode("utf-8", "replace")
        try:
            out = parse_suggestions(json.loads(text))
        except ValueError:
            raise RuntimeError("answer was not JSON (probably a block or consent page)")
        if st is not None:
            st["streak"] = 0
            st["with_results" if out else "empty"] += 1
        return out
    except Exception as e:  # noqa: BLE001
        if st is not None:
            st["errors"] += 1
            st["streak"] += 1
            st["last_error"] = str(e)[:120]
            if st["streak"] >= BREAKER:
                st["disabled"] = True
        return []


def classify_intent(kw: str) -> str:
    k = kw.lower()
    if re.search(r"\b(buy|price|cheap|deal|coupon|under \$?\d+|for sale)\b", k):
        return "transactional"
    if re.search(r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative)\b", k):
        return "commercial"
    if re.search(r"\b(how|why|what|when|can|does|is|are|should|which)\b", k):
        return "informational"
    return "unclear"


def stem(w: str) -> str:
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def content_words(text: str, drop: Optional[set] = None) -> list:
    """Stemmed topic words of a phrase, without intent/glue words (and optional extra words)."""
    drop = drop or set()
    out = []
    for w in re.findall(r"[a-z0-9$]+", text.lower()):
        s = stem(w)
        if w in INTENT_WORDS or s in INTENT_WORDS or s in drop:
            continue
        out.append(s)
    return out


def seed_word_set(seeds: list) -> set:
    return {stem(w) for sd in seeds for w in re.findall(r"[a-z0-9]+", sd.lower()) if w not in INTENT_WORDS}


def _good(kw: str, seed_words: set) -> bool:
    words = re.findall(r"[a-z0-9$]+", kw)
    if len(words) < 2 or len(words) > 10:
        return False
    if any(a == b for a, b in zip(words, words[1:])):          # "best best ..."
        return False
    if len(words[-1]) == 1:                                    # alphabet-suffix echo ("... a")
        return False
    hits = sum(1 for w in words if stem(w) in seed_words)
    return hits >= max(1, len(seed_words) // 2)                # stays on topic


def collect(seeds: list, max_total: int = 90, depth: int = 2, depth2_seeds: int = 12, pause: float = 0.25,
            sources: Optional[list] = None, manual: Optional[list] = None) -> tuple:
    """Return (rows, stats). rows: {keyword, intent, source, search_volume, difficulty}.
    Several engines are asked so one blocked engine does not end the run; `manual` keywords you typed in always count."""
    sources = [x for x in (sources or DEFAULT_SOURCES)]
    stats = Stats(sources)
    seed_words = seed_word_set(seeds)
    seen, rows = set(), []

    def add(k, src, trust=False):
        k = re.sub(r"\s+", " ", k.strip().lower())
        if k and k not in seen and (trust or _good(k, seed_words)) and len(rows) < max_total * 4:
            seen.add(k)
            rows.append({"keyword": k, "intent": classify_intent(k), "source": src,
                         "search_volume": "Not verified", "difficulty": "Not verified"})
            return True
        return False

    for k in manual or []:
        add(k, "manual", trust=True)

    order = {"transactional": 0, "commercial": 1, "informational": 2, "unclear": 3}
    for src in sources:
        found = []
        for seed in seeds:
            queries = [seed] + [f"{p} {seed}" for p in PREFIXES] + [f"{seed} {m}" for m in SUFFIXES] \
                + [f"{seed} {c}" for c in string.ascii_lowercase]
            for q in queries:
                if stats.s[src]["disabled"]:
                    break
                for sug in autocomplete(q, src, stats):
                    if add(sug, f"{src}_autocomplete"):
                        found.append(sug)
                time.sleep(pause)
        if depth >= 2 and not stats.s[src]["disabled"]:
            first = sorted(found, key=lambda k: (order[classify_intent(k)], len(k.split())))[:depth2_seeds]
            for k in first:
                for sug in autocomplete(k, src, stats):
                    add(sug, f"{src}_autocomplete_depth2")
                time.sleep(pause)

    rows.sort(key=lambda r: (order[r["intent"]], len(r["keyword"].split()), r["keyword"]))
    return rows[:max_total], stats


def collect_keywords(seeds: list, max_total: int = 90, depth: int = 2, depth2_seeds: int = 12, pause: float = 0.25,
                     sources: Optional[list] = None, manual: Optional[list] = None) -> list:
    return collect(seeds, max_total, depth, depth2_seeds, pause, sources, manual)[0]


def seasonality(keyword: str, declared_peak_months: list) -> dict:
    """Relative interest by calendar month (US, 5 years). Falls back to declared months."""
    fallback = {"status": "user_declared", "peak_months": sorted(declared_peak_months or []),
                "index_by_month": {},
                "note": "Google Trends unavailable; using the months declared in config.yaml."}
    if not declared_peak_months:
        fallback["status"] = "not_verified"
        fallback["note"] = "Google Trends unavailable and no declared_peak_months in config.yaml."
    try:
        from pytrends.request import TrendReq
        py = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        py.build_payload([keyword], timeframe="today 5-y", geo="US")
        df = py.interest_over_time()
        if df is None or df.empty or keyword not in df:
            return fallback
        monthly = df[keyword].groupby(df.index.month).mean()
        top = monthly.max()
        if not top:
            return fallback
        index = {int(m): round(float(v) / float(top) * 100) for m, v in monthly.items()}
        return {"status": "verified", "peak_months": sorted(m for m, v in index.items() if v >= 70),
                "index_by_month": index,
                "note": f"Google Trends, US, last 5 years, '{keyword}' (relative, 100 = peak month)."}
    except Exception:  # noqa: BLE001
        return fallback
