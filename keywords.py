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

import re
import string
import time
from typing import Optional

import requests

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
PREFIXES = ["best", "how to", "why", "is", "what", "can", "does", "should"]
SUFFIXES = ["for", "vs", "without", "with", "that", "under"]

# Words that say WHAT the searcher wants (intent) or glue a phrase together, not WHAT it is about.
INTENT_WORDS = set("""
best top review reviews vs versus compare comparison cheap cheapest buy price prices deal deals
how to why what can does do is are should a an the of in on for and or with without that your you my
under amazon near me which who where when it this these those
""".split())
STOP = INTENT_WORDS


def autocomplete(term: str, timeout: int = 10) -> list:
    try:
        r = requests.get(SUGGEST_URL,
                         params={"client": "firefox", "q": term, "hl": "en", "gl": "us"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
        return [s for s in r.json()[1] if isinstance(s, str)]
    except Exception:  # noqa: BLE001
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


def collect_keywords(seeds: list, max_total: int = 90, depth: int = 2, depth2_seeds: int = 12,
                     pause: float = 0.25) -> list:
    """Rows: {keyword, intent, source, search_volume, difficulty}."""
    seed_words = seed_word_set(seeds)
    seen, rows = set(), []

    def add(k, src):
        k = re.sub(r"\s+", " ", k.strip().lower())
        if k and k not in seen and _good(k, seed_words) and len(rows) < max_total * 3:
            seen.add(k)
            rows.append({"keyword": k, "intent": classify_intent(k), "source": src,
                         "search_volume": "Not verified", "difficulty": "Not verified"})
            return True
        return False

    for seed in seeds:
        queries = [seed] + [f"{p} {seed}" for p in PREFIXES] + [f"{seed} {m}" for m in SUFFIXES] \
            + [f"{seed} {c}" for c in string.ascii_lowercase]
        for q in queries:
            for s in autocomplete(q):
                add(s, "google_autocomplete")
            time.sleep(pause)

    if depth >= 2:
        order = {"transactional": 0, "commercial": 1, "informational": 2, "unclear": 3}
        first_level = sorted(rows, key=lambda r: order[r["intent"]])[:depth2_seeds]
        for r in first_level:
            for s in autocomplete(r["keyword"]):
                add(s, "google_autocomplete_depth2")
            time.sleep(pause)

    # keep the most useful first: money intents, then shorter (broader) phrases
    order = {"transactional": 0, "commercial": 1, "informational": 2, "unclear": 3}
    rows.sort(key=lambda r: (order[r["intent"]], len(r["keyword"].split()), r["keyword"]))
    return rows[:max_total]


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
