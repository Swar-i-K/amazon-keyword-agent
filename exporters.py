"""
exporters.py
------------
Writes under output/<niche-slug>/:

  keyword_plan.md     the readable plan (publish order, clusters, gaps, cannibalization, FAQ ideas)
  keywords.csv        every keyword with its cluster + role. The SEO agent reads it (`keyword` column)
  clusters.csv        one row per cluster
  content_plan.csv    one row per planned article, in publish order
  research_queue.csv  gap topics for the research agent / article agent
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

QUESTION_START = ("how", "what", "why", "can", "does", "is", "should", "which", "are", "do")


def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "niche")[:maxlen].strip("-") or "niche"


def _season_text(season: dict) -> str:
    peaks = season.get("peak_months") or []
    return f"{season.get('status', 'not_verified')}: peaks {peaks}" if peaks else "Not verified"


def question_keywords(rows: list, limit: int = 15) -> list:
    qs = [r["keyword"] for r in rows if r["keyword"].split()[0] in QUESTION_START]
    return qs[:limit]


def build_report(ctx: dict) -> str:
    cfg, today, plan = ctx["cfg"], ctx["today"], ctx["plan"]
    gaps = [p for p in plan if p["coverage"]["status"] != "covered"]
    out = [f"# Keyword plan: {cfg.get('niche')}",
           f"*Run date:* {today}  |  *Seeds:* {', '.join(cfg.get('seeds', []))}", "",
           "> Search volume and keyword difficulty are **Not verified**: they need a paid tool. Priority is a screening order, not a traffic or revenue forecast.",
           "> Google autocomplete shows what people type, not how often.", "",
           "## 1. Summary",
           f"- {len(ctx['rows'])} keywords collected, grouped into {len(plan)} clusters (one article each).",
           f"- Clustering by: **{ctx['cluster_source']}**.",
           f"- Seasonality: {ctx['season'].get('note', '')} ({ctx['season'].get('status')}).",
           f"- Your existing pages: {ctx['n_pages']} listed. Gaps or partial coverage: **{len(gaps)}** of {len(plan)} clusters.", "",
           "## 2. Publish order",
           "", "| # | Article topic | Primary keyword (one per article) | Intent | Coverage | Season | Priority |",
           "|---|---|---|---|---|---|---|"]
    for i, p in enumerate(plan, 1):
        c, cov = p["cluster"], p["coverage"]
        cov_txt = cov["status"] + (f" ({cov['url']})" if cov["url"] else "")
        score = p["priority"]["score"] if p["priority"]["score"] is not None else "n/a"
        out.append(f"| {i} | {c['name']} | {c['primary']} | {c['intent']} | {cov_txt} | {_season_text(p['season'])} | {score} |")
    out += ["", "Priority blends intent (money intent first), gap (uncovered first), breadth (how many distinct searches Google suggests) and season. "
                "Components with no data are dropped, not guessed."]

    out += ["", "## 3. Clusters"]
    for i, p in enumerate(plan, 1):
        c = p["cluster"]
        out += ["", f"### {i}. {c['name']}", f"**Primary keyword:** {c['primary']}  |  **Intent:** {c['intent']}  |  **Coverage:** {p['coverage']['why']}"]
        if c.get("angle"):
            out.append(f"**Article angle:** {c['angle']}")
        sup = [k for k in c["keywords"] if k != c["primary"]]
        out.append("**Supporting keywords** (use as secondary keywords, headings and FAQs): " + (", ".join(sup[:12]) or "none"))

    out += ["", "## 4. Your existing pages"]
    if not ctx["n_pages"]:
        out += ["", "No pages listed in `site_pages.csv`, so every cluster counts as a gap. Add `url,title,focus_keyword` rows to get coverage and cannibalization checks."]
    elif ctx["cannibal"]:
        out += ["", "**Possible cannibalization** (two of your pages target nearly the same keyword; consider merging or re-targeting one):", "",
                "| Page A | Page B | Keywords | Similarity |", "|---|---|---|---|"]
        out += [f"| {x['a']} | {x['b']} | {x['keyword_a']} / {x['keyword_b']} | {x['similarity']} |" for x in ctx["cannibal"]]
    else:
        out += ["", "No two listed pages share a focus keyword. (Pages without a `focus_keyword` value cannot be checked.)"]
    if ctx.get("overlaps"):
        out += ["", "**Overlap inside this plan** (these clusters have the same angle; write ONE article):"]
        out += [f"- {a} and {b}" for a, b in ctx["overlaps"]]

    qs = question_keywords(ctx["rows"])
    if qs:
        out += ["", "## 5. Question searches (FAQ ideas)", "", *[f"- {q}" for q in qs]]

    out += ["", "## 6. Next steps",
            "1. Take the top row of `research_queue.csv` into the **research agent**: its `search_terms` go in the workflow's search_terms box "
            "(and `seed_keywords` in its config.yaml), then run the article agent on the products it finds.",
            "2. Copy `keywords.csv` into the **SEO agent** repo so it draws secondary keywords from this plan.",
            "3. Give each article its cluster's primary keyword as the focus keyword (`focus_keyword` in the SEO agent's `jobs.csv`).",
            "4. Add every published article to `site_pages.csv` with its focus keyword, then rerun to keep the plan current."]
    if ctx.get("notes"):
        out += ["", "## Run notes", *[f"- {n}" for n in ctx["notes"]]]
    return "\n".join(out) + "\n"


def write_all(base: Path, ctx: dict) -> dict:
    cfg, plan, rows = ctx["cfg"], ctx["plan"], ctx["rows"]
    out = base / slugify(cfg.get("niche", "niche"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "keyword_plan.md").write_text(build_report(ctx), encoding="utf-8")

    member = {}
    for p in plan:
        for k in p["cluster"]["keywords"]:
            member[k] = (p["cluster"]["name"], "primary" if k == p["cluster"]["primary"] else "supporting")
    with open(out / "keywords.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["keyword", "intent", "source", "search_volume", "difficulty", "cluster", "role"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: (member.get(r["keyword"], ("~", ""))[1] != "primary", r["keyword"])):
            c, role = member.get(r["keyword"], ("", ""))
            w.writerow({**r, "cluster": c, "role": role})

    with open(out / "clusters.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cluster", "primary_keyword", "intent", "coverage", "keyword_count", "keywords", "angle"])
        for p in plan:
            c = p["cluster"]
            w.writerow([c["name"], c["primary"], c["intent"], p["coverage"]["status"], len(c["keywords"]),
                        " | ".join(c["keywords"]), c.get("angle", "")])

    with open(out / "content_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "cluster", "primary_keyword", "intent", "coverage", "covered_by", "priority",
                    "season_status", "peak_months", "angle", "clustered_by"])
        for i, p in enumerate(plan, 1):
            c = p["cluster"]
            w.writerow([i, c["name"], c["primary"], c["intent"], p["coverage"]["status"], p["coverage"]["url"],
                        "" if p["priority"]["score"] is None else p["priority"]["score"], p["season"].get("status", ""),
                        " ".join(str(m) for m in p["season"].get("peak_months") or []), c.get("angle", ""), c.get("source", "")])

    with open(out / "research_queue.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["focus_keyword", "suggested_type", "search_terms", "note"])
        w.writeheader()
        w.writerows(ctx["queue"])
    return {"dir": str(out), "clusters": len(plan), "queue": len(ctx["queue"])}
