"""
main.py
-------
Keyword agent. One run = one niche:

  1. expand the seed phrases with free Google autocomplete (depth 2)
  2. group the keywords into clusters, one article per cluster (Groq; deterministic fallback)
  3. compare clusters with your existing posts (site_pages.csv): covered / partial / gap, plus cannibalization
  4. seasonality from Google Trends (falls back to the months you declared)
  5. priority score and publish order
  6. write the plan, keywords.csv (for the other agents) and research_queue.csv

Nothing is published or spent. No search volume or difficulty is claimed.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import yaml

import cluster as clustering
import exporters
import keywords as kw
import llm
import plan as planner

ROOT = Path(__file__).parent
PAUSE = 2          # seconds between Google Trends calls


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if os.environ.get("KEYWORD_SEEDS"):
        cfg["seeds"] = [s.strip() for s in os.environ["KEYWORD_SEEDS"].split(",") if s.strip()]
    if os.environ.get("KEYWORD_NICHE"):
        cfg["niche"] = os.environ["KEYWORD_NICHE"].strip()
    cfg.setdefault("niche", (cfg.get("seeds") or ["niche"])[0])
    cfg.setdefault("seeds", [])
    cfg.setdefault("limits", {})
    for k, v in {"max_keywords": 90, "max_clusters": 12, "expand_depth": 2, "depth2_seeds": 12, "trends_clusters": 6}.items():
        cfg["limits"].setdefault(k, v)
    cfg.setdefault("llm", {"model": "openai/gpt-oss-120b", "max_tokens": 5000})
    cfg.setdefault("weights", {"intent": .3, "gap": .3, "breadth": .2, "season": .2})
    cfg.setdefault("declared_peak_months", [])
    return cfg


def load_pages(path: Path = ROOT / "site_pages.csv") -> list:
    out = []
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                url = (r.get("url") or "").strip()
                if url:
                    out.append({"url": url, "title": (r.get("title") or "").strip(),
                                "focus_keyword": (r.get("focus_keyword") or "").strip().lower()})
    return out


def run(cfg: dict | None = None, pages: list | None = None, out_base: Path | None = None,
        today: str | None = None) -> dict:
    cfg = cfg or load_config()
    today = today or date.today().isoformat()
    month = date.fromisoformat(today).month
    out_base = out_base or ROOT / "output"
    pages = load_pages() if pages is None else pages
    lim, notes = cfg["limits"], []
    seeds = cfg["seeds"]
    if not seeds:
        print("No seeds in config.yaml.")
        return {"status": "failed: no seeds"}

    # 1. keywords
    rows = kw.collect_keywords(seeds, max_total=lim["max_keywords"], depth=lim["expand_depth"],
                               depth2_seeds=lim["depth2_seeds"])
    if len(rows) < 5:
        msg = (f"failed: only {len(rows)} keyword(s) found. Google autocomplete may be blocking this IP, "
               "or the seed is too narrow. Try broader seeds.")
        print(msg)
        return {"status": msg}

    # 2. clusters
    cluster_source = "Groq (validated)"
    try:
        clusters = clustering.cluster_with_llm(rows, cfg["niche"], cfg)
    except Exception as e:  # noqa: BLE001
        reason = str(e) if isinstance(e, llm.LLMUnavailable) else f"LLM clustering failed ({e})"
        notes.append(f"{reason}. Used the built-in rule-based clustering (groups by the most common modifier word); cluster names are plainer.")
        cluster_source = "rule-based fallback"
        clusters = clustering.cluster_deterministic(rows, seeds, lim["max_clusters"])

    # 3. coverage
    seed_words = kw.seed_word_set(seeds)
    cannibal = planner.cannibalization(pages, seed_words)
    overlaps = planner.plan_overlaps(clusters, seed_words)

    # 4. season: one global call; per-cluster calls only if Trends actually works
    season_global = kw.seasonality(seeds[0], cfg["declared_peak_months"])
    per_cluster = {}
    if season_global["status"] == "verified":
        for c in sorted(clusters, key=lambda c: -len(c["keywords"]))[: lim["trends_clusters"]]:
            time.sleep(PAUSE)
            s = kw.seasonality(c["primary"], cfg["declared_peak_months"])
            if s["status"] == "verified":
                per_cluster[c["primary"]] = s
    else:
        notes.append(f"Seasonality: {season_global['note']}")

    # 5. priority
    max_breadth = max(len(c["keywords"]) for c in clusters)
    plan_rows = []
    for c in clusters:
        cov = planner.coverage(c, pages, seed_words)
        season = per_cluster.get(c["primary"], season_global)
        plan_rows.append({"cluster": c, "coverage": cov, "season": season,
                          "priority": planner.priority(c, cov, season, month, cfg["weights"], max_breadth)})
    # gaps and partials first (by score), already-covered topics last
    plan_rows.sort(key=lambda p: (p["coverage"]["status"] == "covered",
                                  -(p["priority"]["score"] if p["priority"]["score"] is not None else -1),
                                  p["cluster"]["name"]))
    queue = planner.research_queue(plan_rows)

    # 6. export
    ctx = {"cfg": cfg, "today": today, "rows": rows, "plan": plan_rows, "season": season_global, "cannibal": cannibal,
           "overlaps": overlaps, "n_pages": len(pages), "cluster_source": cluster_source, "queue": queue, "notes": notes}
    written = exporters.write_all(out_base, ctx)
    summary = {"status": "success", "output": written["dir"], "keywords": len(rows), "clusters": len(plan_rows),
               "research_queue": len(queue), "clustered_by": cluster_source, "notes": notes}
    (ROOT / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "output", "keywords", "clusters", "clustered_by")}, indent=2))
    return summary


if __name__ == "__main__":
    result = run()
    sys.exit(0 if result.get("status") == "success" else 1)
