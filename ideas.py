"""
ideas.py
--------
Content ideas from product information. IDEAS ONLY: no articles, scripts or captions are written here.

  input : products.csv (name, asin, amazon_link, category, seed_keyword), or the IDEAS_PRODUCTS env var (names only)
  output: output/<category>/content_ideas.md, content_ideas.xlsx, seed_phrases.txt   (one folder per category)

Products are grouped by `category`. For each category: an optional Perplexity web-research step (what shoppers ask, needs
PERPLEXITY_API_KEY), then one model call per platform (WordPress article, YouTube, Facebook/Instagram, Pinterest) through the
provider chain in providers.py (Gemini, then Groq). Each idea has a different angle (buyer intent, problem solving, product
specific, comparison, audience, seasonal). Python validates the answer: unknown angles are labelled, duplicates and
"I tested it" claims are dropped, product ids must be real. With no AI key (or if the models keep failing) a plain template
list is used and labelled "template".

Ideas are NOT checked for demand. Feed `seed_phrases.txt` to the keyword agent (main.py) to see which ones people search.
"""
from __future__ import annotations

import calendar
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import exporters
import llm
import main as keyword_main
import providers
import tables

ROOT = Path(__file__).parent

PLATFORMS = {
    "article": ("WordPress article",
                "Search-driven articles. Mix buyer intent (best X, X vs Y, under a price, is X worth it), problem solving "
                "(how to fix or choose) and product-specific topics (who it suits, alternatives, buying mistakes). "
                "Titles should read like what a shopper would type into Google."),
    "youtube": ("YouTube video",
                "Video ideas with a clear hook: comparisons, buying guides, common mistakes, myth busting, problem walk-throughs. "
                "Each title must work as a YouTube title."),
    "social": ("Facebook/Instagram post",
               "Short post ideas that fit one post or one short reel: a single tip, myth vs fact, a before/after problem, "
               "a this-or-that poll, a quick checklist."),
    "pinterest": ("Pinterest pin",
                  "Pins people save: checklists, comparison charts, 'how to choose' graphics, problem/solution visuals, "
                  "seasonal prep lists. Titles should be descriptive and search friendly."),
}
ANGLES = ("buyer_intent", "problem_solving", "product_specific", "comparison", "audience", "seasonal")

# the owner has not said they used the products, so ideas must not promise hands-on testing
TESTING_CLAIM = re.compile(
    r"\b(tested|hands[- ]on|i tried|we tried|i used|we used|i bought|we bought|my experience|our experience)\b", re.I)

DEFAULTS = {"products_file": "products.csv", "platforms": list(PLATFORMS), "per_platform": 8, "max_products": 6,
            "max_categories": 3, "hands_on_testing": False, "max_tokens": 4000, "providers": ["gemini", "groq"],
            "research": True, "research_effort": "fast"}


# ------------------------------------------------------------------ input
def ideas_cfg(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update(cfg.get("ideas") or {})
    out["platforms"] = [p for p in out["platforms"] if p in PLATFORMS] or list(PLATFORMS)
    out["per_platform"] = max(1, min(20, int(out["per_platform"])))
    out["max_products"] = max(1, min(10, int(out["max_products"])))
    out["max_categories"] = max(1, min(6, int(out["max_categories"])))
    return out


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
LINK_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})")
HEADER_ALIASES = {"amazon_link": "amazon_link", "amz_link": "amazon_link", "link": "amazon_link", "url": "amazon_link",
                  "amazon_url": "amazon_link", "seed_keyword": "seed_keyword", "seed": "seed_keyword",
                  "seed_keywords": "seed_keyword", "product_name": "name", "title": "name", "reviews": "review_count"}


def _norm_header(h: str) -> str:
    h = re.sub(r"[^a-z0-9]+", "_", (h or "").lower()).strip("_")
    return HEADER_ALIASES.get(h, h)


def make_product(name, asin="", link="", category="", seed_keyword="", price="", rating="", review_count="",
                 features=None, notes="") -> dict:
    asin, link = _clean(asin).upper(), _clean(link)
    if not ASIN_RE.match(asin):
        m = LINK_ASIN_RE.search(link)
        asin = m.group(1).upper() if m else ""
    if not link and asin:
        link = f"https://www.amazon.com/dp/{asin}"
    return {"name": _clean(name), "asin": asin, "link": link, "category": _clean(category),
            "seed_keyword": _clean(seed_keyword).lower(), "price": _clean(price), "rating": _clean(rating),
            "review_count": _clean(review_count), "features": features or [], "notes": _clean(notes)}


def load_products(path: Path, limit: int = 60) -> list:
    """products.csv columns: name (required), asin, amazon_link, category, seed_keyword.
    Optional extras: price, rating, review_count, features (separate with ;), notes."""
    out = []
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(f):
                r = {_norm_header(k): v for k, v in raw.items() if k}
                if not _clean(r.get("name")):
                    continue
                out.append(make_product(
                    r.get("name"), r.get("asin"), r.get("amazon_link"), r.get("category"), r.get("seed_keyword"),
                    r.get("price"), r.get("rating"), r.get("review_count"),
                    [x.strip() for x in (r.get("features") or "").split(";") if x.strip()], r.get("notes")))
    return out[:limit]


def products_from_env(value: str, limit: int = 60) -> list:
    """IDEAS_PRODUCTS="Name A; Name B": names only, handy when you do not want to edit products.csv."""
    return [make_product(n) for n in (value or "").split(";") if n.strip()][:limit]


def group_by_category(products: list, default_niche: str, max_categories: int, max_products: int) -> tuple:
    """-> ([(category, [products])], notes). Products without a category use the niche from config.yaml."""
    groups, notes = {}, []
    for p in products:
        groups.setdefault(p["category"] or default_niche, []).append(p)
    items = list(groups.items())[:max_categories] or [(default_niche, [])]
    if len(groups) > max_categories:
        notes.append(f"Only the first {max_categories} categories were used ({len(groups)} found); raise ideas.max_categories to use more.")
    out = []
    for cat, ps in items:
        if len(ps) > max_products:
            notes.append(f"Category '{cat}': only the first {max_products} of {len(ps)} products were used.")
        out.append((cat, ps[:max_products]))
    return out, notes


# ------------------------------------------------------------------ prompts
def _product_lines(products: list) -> str:
    if not products:
        return "(no product details given: use the category only and leave product_ids empty)"
    out = []
    for i, p in enumerate(products):
        bits = [f"{i}: {p['name']}"]
        if p["seed_keyword"]:
            bits.append(f"owner's seed keyword: {p['seed_keyword']}")
        if p["price"]:
            bits.append(f"price {p['price']}")
        if p["rating"]:
            bits.append(f"rating {p['rating']}" + (f" ({p['review_count']} reviews)" if p["review_count"] else ""))
        if p["features"]:
            bits.append("features: " + "; ".join(p["features"][:8]))
        if p["notes"]:
            bits.append("notes: " + p["notes"])
        out.append(" | ".join(bits))
    return "\n".join(out)


def research_prompt(category: str, products: list) -> str:
    names = ", ".join(p["name"] for p in products[:6])
    return (f"Use web search. What do US shoppers actually ask, worry about and get wrong when buying {category}"
            + (f" (for example {names})" if names else "") + "? "
            "List up to 12 short bullet points: real questions, common problems, buying mistakes, use cases and seasonal reasons to buy. "
            "No brand praise, no prices, no product claims. Plain bullets only.")


def build_prompt(platform: str, products: list, niche: str, n: int, peak_months: list, research_notes: str = "") -> str:
    label, guide = PLATFORMS[platform]
    season = ""
    if peak_months:
        season = "Demand peaks in: " + ", ".join(calendar.month_abbr[m] for m in peak_months if 1 <= m <= 12) + ".\n"
    multi = ("- At least two ideas must use TWO OR MORE products (comparison, combination or 'which one for whom').\n"
             if len(products) > 1 else "")
    seeds = ("- Where it fits, build on each product's owner's seed keyword.\n"
             if any(p["seed_keyword"] for p in products) else "")
    notes = (f"\nMARKET NOTES from live web search (unverified; use them only to decide WHAT shoppers want covered, "
             f"never as product facts):\n{research_notes}\n" if research_notes else "")
    return f"""You are a content strategist for an affiliate site about: {niche} (US shoppers).
Give {n} content IDEAS (not drafts) for this platform. Platform: {label}
{guide}
{season}
PRODUCTS (id: details):
{_product_lines(products)}
{notes}
Return ONE JSON object and nothing else:
{{"ideas": [{{"angle": "buyer_intent|problem_solving|product_specific|comparison|audience|seasonal",
  "title": "<working title, max 90 characters>",
  "problem": "<one sentence: the shopper's problem or the decision this helps with>",
  "product_ids": [<ids of the products it uses, may be empty>],
  "seed_phrase": "<2-5 words a shopper would type into a search box for this idea>"}}]}}

RULES
- Every idea must have a clearly different angle or problem. Spread the ideas across the angles above; do not repeat the same idea with new wording.
- Use only the product facts given. Never invent a price, rating, spec, brand or test result.
- Do not imply the owner tested, used or owns the products (no "tested", "hands-on", "I tried", "our experience").
- No guarantees or health claims.
{multi}{seeds}- Exactly {n} ideas.
"""


def validate_ideas(raw: dict, platform: str, products: list, n: int, hands_on: bool = False) -> tuple:
    """-> (ideas, dropped). The model can never add a product that is not in the list."""
    seen, ideas, dropped = set(), [], 0
    for it in (raw.get("ideas") or []):
        if not isinstance(it, dict):
            dropped += 1
            continue
        title, problem = _clean(it.get("title"))[:110], _clean(it.get("problem"))[:220]
        key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if not title or key in seen:
            dropped += 1
            continue
        if not hands_on and TESTING_CLAIM.search(title + " " + problem):
            dropped += 1
            continue
        seen.add(key)
        angle = _clean(it.get("angle")).lower()
        ids = []
        for x in it.get("product_ids") or []:
            try:
                x = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= x < len(products) and x not in ids:
                ids.append(x)
        seed = re.sub(r"[^a-z0-9$ ]+", " ", _clean(it.get("seed_phrase")).lower())
        seed = _clean(seed)
        if not 2 <= len(seed.split()) <= 6:
            seed = " ".join(key.split()[:5])
        ideas.append({"platform": platform, "angle": angle if angle in ANGLES else "other", "title": title, "problem": problem,
                      "products": [products[i]["name"] for i in ids], "links": [products[i].get("link", "") for i in ids],
                      "seed_phrase": seed, "source": "llm"})
        if len(ideas) >= n:
            break
    return ideas, dropped


# ------------------------------------------------------------------ fallback (no model)
def template_ideas(platform: str, niche: str, products: list, n: int) -> list:
    a = products[0]["name"] if products else niche
    b = products[1]["name"] if len(products) > 1 else ""
    pair = bool(b)
    T = {
        "article": [
            ("buyer_intent", f"Best {niche}: how to choose the right one", [], f"best {niche}"),
            ("product_specific", f"Is {a} worth it? Pros, cons and who it suits", [0] if products else [], f"is {a} worth it"),
            ("comparison", f"{a} vs {b}: which one should you buy?", [0, 1], f"{a} vs {b}") if pair else None,
            ("problem_solving", f"{niche}: common buying mistakes to avoid", [], f"{niche} buying mistakes"),
            ("buyer_intent", f"Cheap vs premium {niche}: is the extra cost worth it?", [], f"cheap vs premium {niche}"),
            ("product_specific", f"Alternatives to {a}", [0] if products else [], f"{a} alternatives"),
            ("audience", f"{niche} for small budgets and tight spaces: what to look for", [], f"{niche} small budget"),
        ],
        "youtube": [
            ("buyer_intent", f"{niche}: what to look for before you buy", [], f"{niche} buying guide"),
            ("comparison", f"{a} vs {b} compared on features and price", [0, 1], f"{a} vs {b}") if pair else None,
            ("problem_solving", f"5 mistakes people make when buying {niche}", [], f"{niche} mistakes"),
            ("product_specific", f"Is {a} worth the money?", [0] if products else [], f"is {a} worth it"),
        ],
        "social": [
            ("problem_solving", f"Quick tip: how to pick {niche}", [], f"how to pick {niche}"),
            ("audience", f"Poll: which matters more in {niche}, price or features?", [], f"{niche} price or features"),
            ("comparison", f"Which would you choose: {a} or {b}?", [0, 1], f"{a} vs {b}") if pair else None,
            ("buyer_intent", f"Myth vs fact: {niche}", [], f"{niche} myths"),
        ],
        "pinterest": [
            ("buyer_intent", f"{niche} buying checklist", [], f"{niche} checklist"),
            ("problem_solving", f"How to choose {niche} (quick guide)", [], f"how to choose {niche}"),
            ("comparison", f"{a} vs {b} at a glance", [0, 1], f"{a} vs {b}") if pair else None,
            ("seasonal", f"Get ready for the season: {niche} to-do list", [], f"{niche} season prep"),
        ],
    }[platform]
    out = []
    for t in T:
        if not t:
            continue
        angle, title, ids, seed = t
        ids = [i for i in ids if i < len(products)]
        out.append({"platform": platform, "angle": angle, "title": title[:110], "problem": "",
                    "products": [products[i]["name"] for i in ids], "links": [products[i].get("link", "") for i in ids],
                    "seed_phrase": re.sub(r"[^a-z0-9$ ]+", " ", seed.lower()).strip(), "source": "template"})
    return out[:n]


# ------------------------------------------------------------------ run + export
def write_outputs(out: Path, ideas: list, ctx: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    headers = ["platform", "angle", "title", "problem", "products", "product_links", "seed_phrase", "source"]
    rows = [[PLATFORMS[i["platform"]][0], i["angle"], i["title"], i["problem"], " | ".join(i["products"]),
             " | ".join(x for x in i["links"] if x), i["seed_phrase"], i["source"]] for i in ideas]
    tables.write_tables(out, "content_ideas.xlsx", [("Ideas", "content_ideas.csv", headers, rows)], ctx["cfg"])
    seeds = []
    for p in ctx["products"]:                                   # your own seed keywords first, then the idea seeds
        if p["seed_keyword"] and p["seed_keyword"] not in seeds:
            seeds.append(p["seed_keyword"])
    for i in ideas:
        if i["seed_phrase"] and i["seed_phrase"] not in seeds:
            seeds.append(i["seed_phrase"])
    (out / "seed_phrases.txt").write_text("\n".join(seeds) + ("\n" if seeds else ""), encoding="utf-8")

    link_of = {p["name"]: p["link"] for p in ctx["products"]}

    def prod(name: str) -> str:
        return f"[{name}]({link_of[name]})" if link_of.get(name) else name

    md = [f"# Content ideas: {ctx['niche']}", f"*Run date:* {ctx['today']}  |  *Products:* "
          + (", ".join(p["name"] for p in ctx["products"]) or "none (category only)"), "",
          "> These are **ideas, not validated demand**. Nobody has checked search volume. To see which ones people actually search, "
          "run the keyword agent with `seed_phrases.txt` (paste into `manual_keywords.txt`, or use 1-3 of them as seeds).", ""]
    for n in ctx["notes"]:
        md.append(f"> Note: {n}")
    if ctx.get("research"):
        md += ["", "<details><summary>Shopper research used for these ideas (Perplexity web search, unverified)</summary>", "",
               ctx["research"], "", "</details>"]
    for platform in ctx["platforms"]:
        mine = [i for i in ideas if i["platform"] == platform]
        md += ["", f"## {PLATFORMS[platform][0]} ({len(mine)})"]
        for angle in list(ANGLES) + ["other"]:
            sub = [i for i in mine if i["angle"] == angle]
            if not sub:
                continue
            md += ["", f"**{angle.replace('_', ' ').title()}**"]
            for i in sub:
                line = f"- **{i['title']}**"
                if i["problem"]:
                    line += f": {i['problem']}"
                if i["products"]:
                    line += f" *(products: {', '.join(prod(n) for n in i['products'])})*"
                line += f"  `seed: {i['seed_phrase']}`" + (" *(template)*" if i["source"] == "template" else "")
                md.append(line)
    (out / "content_ideas.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def _save_summary(summary: dict) -> None:
    (ROOT / "ideas_run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_category(cfg: dict, icfg: dict, category: str, products: list, out_base: Path, today: str) -> dict:
    notes, used = [], {}
    model_cfg = {**cfg, "ideas": {**(cfg.get("ideas") or {}), "providers": icfg["providers"]}}

    # optional web research (Perplexity): what shoppers ask. Costs money per request, so it only runs when the key is set.
    research_notes = ""
    if icfg["research"] and providers.has_key("perplexity"):
        try:
            research_notes = providers.research(research_prompt(category, products), icfg["research_effort"])
            used["perplexity"] = 1
        except Exception as e:  # noqa: BLE001
            notes.append(f"Perplexity research skipped ({e}).")

    ideas, dropped, ai_down = [], 0, False
    for platform in icfg["platforms"]:
        got, bad = None, set()
        if not ai_down:
            for _ in range(2):
                try:
                    prompt = build_prompt(platform, products, category, icfg["per_platform"],
                                          cfg.get("declared_peak_months") or [], research_notes)
                    text, who = providers.generate(prompt, model_cfg, icfg["max_tokens"], 0.7, exclude=bad)
                    try:
                        got, d = validate_ideas(llm.extract_json(text), platform, products, icfg["per_platform"], icfg["hands_on_testing"])
                    except Exception:
                        bad.add(who)
                        raise
                    dropped += d
                    if got:
                        used[who] = used.get(who, 0) + 1
                        break
                    bad.add(who)
                except llm.LLMUnavailable as e:
                    notes.append(f"{e}. Used plain template ideas for every platform.")
                    ai_down = True
                    break
                except providers.ProviderError as e:                # every provider failed: do not hammer them again
                    notes.append(f"{PLATFORMS[platform][0]}: {e}.")
                    break
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{PLATFORMS[platform][0]}: model answer failed ({e}).")
        if not got:
            if not ai_down:
                notes.append(f"{PLATFORMS[platform][0]}: used plain template ideas instead.")
            got = template_ideas(platform, category, products, icfg["per_platform"])
        ideas += got
    if dropped:
        notes.append(f"{dropped} model idea(s) were dropped (duplicates, or wording that implies you tested the product).")

    ctx = {"niche": category, "today": today, "products": products, "platforms": icfg["platforms"], "notes": notes,
           "research": research_notes, "cfg": cfg}
    out = out_base / exporters.slugify(category)
    write_outputs(out, ideas, ctx)
    return {"category": category, "output": str(out), "ideas": len(ideas), "products": [p["name"] for p in products],
            "by_platform": {p: sum(1 for i in ideas if i["platform"] == p) for p in icfg["platforms"]},
            "answered_by": used, "notes": notes,
            "generated_by": "AI (validated)" if any(i["source"] == "llm" for i in ideas) else "template"}


def run(cfg: dict | None = None, products: list | None = None, out_base: Path | None = None,
        today: str | None = None) -> dict:
    cfg = cfg or keyword_main.load_config()
    icfg = ideas_cfg(cfg)
    today = today or date.today().isoformat()
    out_base = out_base or ROOT / "output"

    if products is None:
        env = os.environ.get("IDEAS_PRODUCTS", "").strip()
        products = products_from_env(env) if env else load_products(ROOT / icfg["products_file"])
    notes = []
    if not products:
        notes.append("No products found (products.csv is empty and IDEAS_PRODUCTS is not set), so the ideas use the category only.")
    groups, gnotes = group_by_category(products, cfg["niche"], icfg["max_categories"], icfg["max_products"])
    notes += gnotes

    results = [_run_category(cfg, icfg, cat, ps, out_base, today) for cat, ps in groups]
    summary = {"status": "success", "categories": results, "ideas": sum(r["ideas"] for r in results), "notes": notes}
    _save_summary(summary)
    print(json.dumps({"status": "success", "ideas": summary["ideas"],
                      "categories": [{k: r[k] for k in ("category", "output", "ideas", "generated_by", "answered_by")} for r in results]},
                     indent=2))
    return summary


if __name__ == "__main__":
    result = run()
    sys.exit(0 if result.get("status") == "success" else 1)
