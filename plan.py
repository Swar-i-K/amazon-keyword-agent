"""
plan.py
-------
Turns clusters into a content plan:

  * coverage      compares each cluster with your existing posts (site_pages.csv): covered / partial / gap
  * cannibalization  existing posts that target (almost) the same keyword
  * priority      transparent weighted score; components with no data are dropped and re-weighted
  * research queue  the gap topics, ready for the research agent

Everything here is plain Python. Priority is a screening order for what to publish first, not a
traffic or revenue forecast: there is no search volume or difficulty data.
"""
from __future__ import annotations

from typing import Optional

import keywords as kw

INTENT_VALUE = {"transactional": 1.0, "commercial": 1.0, "informational": 0.4, "unclear": 0.3}
COVERAGE_VALUE = {"gap": 1.0, "partial": 0.4, "covered": 0.0}


def _words(text: str, seed_words: set) -> set:
    """Topic words of a phrase minus the niche's own seed words (what is left is the angle)."""
    return set(kw.content_words(text, drop=seed_words))


# ------------------------------------------------------------------ coverage
def coverage(cluster: dict, pages: list, seed_words: set) -> dict:
    """covered: a page already targets this angle. partial: a page touches it. gap: nothing does."""
    angle = _words(cluster["primary"], seed_words)
    best = {"status": "gap", "url": "", "page_title": "", "why": "No existing page matches this angle."}
    for p in pages:
        text = " ".join(x for x in (p.get("focus_keyword"), p.get("title")) if x)
        page_words = set(kw.content_words(text))
        on_topic = len(page_words & seed_words) >= max(1, len(seed_words) // 2)
        if not on_topic:
            continue
        if not angle:                                   # core cluster: the page must target the main topic itself
            fk = (p.get("focus_keyword") or "").lower().strip()
            if fk and (cluster["primary"] in fk or fk in cluster["primary"] or not _words(fk, seed_words)):
                return {"status": "covered", "url": p["url"], "page_title": p.get("title", ""),
                        "why": "An existing page's focus keyword targets the core topic."}
            if best["status"] == "gap":
                best = {"status": "partial", "url": p["url"], "page_title": p.get("title", ""),
                        "why": "A page on this topic exists, but its focus keyword targets a narrower angle (or is not listed)."}
            continue
        hit = angle & page_words
        if hit == angle:
            return {"status": "covered", "url": p["url"], "page_title": p.get("title", ""),
                    "why": f"Existing page already covers: {', '.join(sorted(angle))}."}
        if hit and best["status"] == "gap":
            best = {"status": "partial", "url": p["url"], "page_title": p.get("title", ""),
                    "why": f"Existing page touches '{', '.join(sorted(hit))}' but not all of '{', '.join(sorted(angle))}'."}
    return best


def cannibalization(pages: list, seed_words: set) -> list:
    """Existing posts whose focus keywords are the same or nearly the same."""
    withkw = [p for p in pages if (p.get("focus_keyword") or "").strip()]
    out = []
    for i in range(len(withkw)):
        for j in range(i + 1, len(withkw)):
            a, b = withkw[i], withkw[j]
            wa, wb = set(kw.content_words(a["focus_keyword"])), set(kw.content_words(b["focus_keyword"]))
            if not wa or not wb:
                continue
            jac = len(wa & wb) / len(wa | wb)
            if jac >= 0.8 or wa == wb:
                out.append({"a": a["url"], "b": b["url"], "keyword_a": a["focus_keyword"], "keyword_b": b["focus_keyword"],
                            "similarity": round(jac, 2)})
    return out


def plan_overlaps(clusters: list, seed_words: set) -> list:
    """Two clusters in THIS plan whose primaries target the same angle (should be merged into one article)."""
    out = []
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            wa, wb = _words(clusters[i]["primary"], seed_words), _words(clusters[j]["primary"], seed_words)
            if wa and wb and wa == wb:
                out.append((clusters[i]["name"], clusters[j]["name"]))
    return out


# ------------------------------------------------------------------ season
def in_season(season: dict, month: int) -> Optional[float]:
    peaks = season.get("peak_months") or []
    if not peaks:
        return None
    nxt = month % 12 + 1
    return 1.0 if (month in peaks or nxt in peaks) else 0.4


# ------------------------------------------------------------------ priority
def priority(cluster: dict, cov: dict, season: dict, month: int, weights: dict, max_breadth: int) -> dict:
    comps = {
        "intent": INTENT_VALUE.get(cluster["intent"], 0.3),
        "gap": COVERAGE_VALUE[cov["status"]],
        "breadth": min(1.0, len(cluster["keywords"]) / max(1, min(10, max_breadth))),
        "season": in_season(season, month),
    }
    used = {k: v for k, v in comps.items() if v is not None and weights.get(k, 0) > 0}
    wsum = sum(weights[k] for k in used)
    score = round(100 * sum(weights[k] * v for k, v in used.items()) / wsum, 1) if wsum else None
    return {"score": score, "components": {k: (None if v is None else round(v, 2)) for k, v in comps.items()},
            "components_used": sorted(used)}


def suggested_type(primary: str) -> str:
    return "vs" if any(t in primary.lower().split() for t in ("vs", "versus", "compare", "comparison")) else "single"


def research_queue(plan_rows: list, limit: int = 8) -> list:
    """Gap and partial topics for the research agent (search terms) and the article agent (focus keyword)."""
    out = []
    for r in plan_rows:
        if r["coverage"]["status"] == "covered":
            continue
        out.append({"focus_keyword": r["cluster"]["primary"], "suggested_type": suggested_type(r["cluster"]["primary"]),
                    "search_terms": r["cluster"]["primary"], "note": r["cluster"].get("angle") or r["cluster"]["name"]})
        if len(out) >= limit:
            break
    return out
