"""
Offline tests: no network, no API key.   Run:  python tests/test_offline.py
Autocomplete and the model are faked, so these tests prove the cleaning, clustering, validation, coverage,
scoring and export logic, not the quality of real Google or Groq results.
"""
import csv
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cluster  # noqa: E402
import exporters  # noqa: E402
import keywords as kw  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import plan  # noqa: E402

SEEDS = ["door draft stopper"]
SEED_WORDS = kw.seed_word_set(SEEDS)

BASE = [
    "door draft stopper", "best door draft stopper", "best door draft stopper for renters",
    "door draft stopper for renters", "door draft stopper without tools", "door draft stopper for apartment",
    "apartment door draft stopper", "door draft stopper for uneven floor", "door draft stopper for uneven gap",
    "best door draft stopper for carpet", "door draft stopper for carpet", "how to stop draft under door stopper",
    "how to use a door draft stopper", "does door draft stopper work", "door draft stopper vs door sweep",
    "cheap door draft stopper", "door draft stopper under $20", "door draft stopper that stays in place",
    "best door draft stopper that stays in place", "pizza near me", "door draft stopper b", "best best door draft stopper",
]


def fake_ac(q, source="google", stats=None, timeout=10):
    return list(BASE) if "draft" in q else []


def with_ac(fn):
    orig = kw.autocomplete
    kw.autocomplete = fake_ac
    try:
        return fn()
    finally:
        kw.autocomplete = orig


def rows():
    return with_ac(lambda: kw.collect_keywords(SEEDS, max_total=90, depth=2, depth2_seeds=3, pause=0, sources=["google"]))


# ------------------------------------------------------------------ keywords
def test_collect_filters_and_dedupes():
    r = rows()
    ks = [x["keyword"] for x in r]
    assert len(ks) == len(set(ks))
    assert "pizza near me" not in ks and "door draft stopper b" not in ks and "best best door draft stopper" not in ks
    assert "door draft stopper for renters" in ks
    assert all(x["search_volume"] == "Not verified" and x["difficulty"] == "Not verified" for x in r)
    assert r[0]["intent"] in ("transactional", "commercial")           # money intents first


def test_intent_and_stemming():
    assert kw.classify_intent("best door draft stopper") == "commercial"
    assert kw.classify_intent("door draft stopper under $20") == "transactional"
    assert kw.classify_intent("how to use a door draft stopper") == "informational"
    assert kw.stem("renters") == "renter" and kw.stem("glass") == "glass" and kw.stem("batteries") == "battery"
    assert kw.content_words("best door draft stoppers for renters", drop=SEED_WORDS) == ["renter"]


def test_seasonality_falls_back():
    s = kw.seasonality("door draft stopper", [11, 12])
    assert s["status"] in ("user_declared", "verified")
    s2 = kw.seasonality("door draft stopper", [])
    assert s2["status"] in ("not_verified", "verified")


# ------------------------------------------------------------------ clustering
def test_deterministic_clusters_cover_every_keyword_once():
    r = rows()
    cl = cluster.cluster_deterministic(r, SEEDS, max_clusters=12)
    all_k = [k for c in cl for k in c["keywords"]]
    assert sorted(all_k) == sorted(x["keyword"] for x in r)               # nothing lost, nothing duplicated
    for c in cl:
        assert c["primary"] in c["keywords"] and c["intent"] in cluster.ALLOWED_INTENT
    names = {c["name"] for c in cl}
    assert any("renter" in n.lower() for n in names) or any("renter" in c["primary"] for c in cl)


def test_cluster_cap_folds_into_long_tail():
    r = rows()
    cl = cluster.cluster_deterministic(r, SEEDS, max_clusters=2)
    assert len(cl) <= 2
    assert sorted(k for c in cl for k in c["keywords"]) == sorted(x["keyword"] for x in r)


def test_primary_prefers_money_intent_then_short():
    r = rows()
    by = {x["keyword"]: x for x in r}
    members = ["how to use a door draft stopper", "best door draft stopper for renters", "door draft stopper for renters"]
    assert cluster.pick_primary(members, by, "renter") == "door draft stopper for renters" or \
        cluster.pick_primary(members, by, "renter") == "best door draft stopper for renters"
    assert cluster.pick_primary(members, by, "renter") != "how to use a door draft stopper"


def test_validate_clusters_guards():
    r = rows()
    n = len(r)
    raw = {"clusters": [
        {"name": "Renters", "primary_id": 0, "keyword_ids": [0, 1, 2, 999, -5, "x"], "intent": "commercial", "angle": "a"},
        {"name": "Duplicate use", "primary_id": 1, "keyword_ids": [1, 2, 3], "intent": "bogus", "angle": "b"},   # 1, 2 already used
        {"name": "Empty", "primary_id": 4, "keyword_ids": [], "intent": "commercial"},
    ]}
    cl = cluster.validate_clusters(raw, r, 12)
    flat = [k for c in cl for k in c["keywords"]]
    assert sorted(flat) == sorted(x["keyword"] for x in r) and len(flat) == n          # forgotten keywords still assigned
    renters = [c for c in cl if c["name"] == "Renters"][0]
    assert renters["primary"] == r[0]["keyword"]
    second = [c for c in cl if c["name"] == "Duplicate use"][0]
    assert second["keywords"] == [r[3]["keyword"]] or r[3]["keyword"] in second["keywords"]
    assert second["intent"] in cluster.ALLOWED_INTENT and second["intent"] != "bogus"
    # primary_id that is not in the cluster's own ids is replaced
    raw2 = {"clusters": [{"name": "X", "primary_id": 5, "keyword_ids": [0, 1], "intent": "commercial"}]}
    cl2 = cluster.validate_clusters(raw2, r, 12)
    x = [c for c in cl2 if c["name"] == "X"][0]
    assert x["primary"] in x["keywords"] and x["primary"] != r[5]["keyword"]
    try:
        cluster.validate_clusters({"clusters": []}, r)
        assert False
    except ValueError:
        pass


# ------------------------------------------------------------------ coverage / cannibalization
def _cl(primary, intent="commercial", n=3):
    return {"name": primary, "primary": primary, "intent": intent, "keywords": [primary] * n, "angle": ""}


def test_coverage_statuses():
    pages = [
        {"url": "https://s.com/renters", "title": "Draft Stoppers for Renters", "focus_keyword": "door draft stopper for renters"},
        {"url": "https://s.com/gap", "title": "Fixing an uneven gap", "focus_keyword": "door draft stopper for uneven gap"},
        {"url": "https://s.com/core", "title": "Door draft stopper guide", "focus_keyword": "door draft stopper"},
    ]
    assert plan.coverage(_cl("door draft stopper for renters"), pages, SEED_WORDS)["status"] == "covered"
    part = plan.coverage(_cl("door draft stopper for uneven floor"), pages, SEED_WORDS)
    assert part["status"] == "partial" and part["url"] == "https://s.com/gap"
    assert plan.coverage(_cl("door draft stopper for carpet"), pages, SEED_WORDS)["status"] == "gap"
    assert plan.coverage(_cl("best door draft stopper"), pages, SEED_WORDS)["status"] == "covered"      # core cluster
    assert plan.coverage(_cl("door draft stopper for renters"), [], SEED_WORDS)["status"] == "gap"
    off_topic = [{"url": "u", "title": "Best pizza ovens", "focus_keyword": "pizza oven for renters"}]
    assert plan.coverage(_cl("door draft stopper for renters"), off_topic, SEED_WORDS)["status"] == "gap"


def test_core_cluster_without_focus_keyword_is_partial():
    pages = [{"url": "https://s.com/x", "title": "Door draft stopper roundup", "focus_keyword": ""}]
    assert plan.coverage(_cl("best door draft stopper"), pages, SEED_WORDS)["status"] == "partial"


def test_cannibalization():
    pages = [
        {"url": "a", "title": "", "focus_keyword": "best door draft stopper"},
        {"url": "b", "title": "", "focus_keyword": "door draft stopper best"},
        {"url": "c", "title": "", "focus_keyword": "door draft stopper for renters"},
        {"url": "d", "title": "", "focus_keyword": ""},
    ]
    found = plan.cannibalization(pages, SEED_WORDS)
    assert len(found) == 1 and {found[0]["a"], found[0]["b"]} == {"a", "b"}


def test_plan_overlaps():
    cl = [_cl("door draft stopper for renters"), _cl("best door draft stopper for renter"), _cl("door draft stopper for carpet")]
    cl[0]["name"], cl[1]["name"] = "A", "B"
    assert plan.plan_overlaps(cl, SEED_WORDS) == [("A", "B")]


# ------------------------------------------------------------------ priority
W = {"intent": .3, "gap": .3, "breadth": .2, "season": .2}


def test_priority_ordering_and_dropped_components():
    peak = {"status": "user_declared", "peak_months": [10, 11]}
    gap = {"status": "gap"}
    cov = {"status": "covered"}
    a = plan.priority(_cl("x", "commercial", 8), gap, peak, 10, W, 10)
    b = plan.priority(_cl("x", "commercial", 8), cov, peak, 10, W, 10)
    c = plan.priority(_cl("x", "informational", 8), gap, peak, 10, W, 10)
    assert a["score"] > b["score"] and a["score"] > c["score"]               # gaps and money intent first
    no_season = plan.priority(_cl("x", "commercial", 8), gap, {"peak_months": []}, 10, W, 10)
    assert "season" not in no_season["components_used"] and no_season["score"] is not None   # dropped, not zeroed
    assert plan.in_season({"peak_months": [11]}, 10) == 1.0               # next month is a peak
    assert plan.in_season({"peak_months": [1]}, 6) == 0.4
    assert plan.in_season({"peak_months": []}, 6) is None


def test_research_queue():
    rows_ = [{"cluster": {"primary": "door draft stopper vs door sweep", "angle": "a", "name": "n"}, "coverage": {"status": "gap"}},
             {"cluster": {"primary": "door draft stopper for renters", "angle": "", "name": "Renters"}, "coverage": {"status": "covered"}},
             {"cluster": {"primary": "door draft stopper for carpet", "angle": "", "name": "Carpet"}, "coverage": {"status": "partial"}}]
    q = plan.research_queue(rows_)
    assert [x["focus_keyword"] for x in q] == ["door draft stopper vs door sweep", "door draft stopper for carpet"]
    assert q[0]["suggested_type"] == "vs" and q[1]["suggested_type"] == "single"


# ------------------------------------------------------------------ fake model + end to end
def fake_llm_factory(bad=False):
    def fake(prompt, model, max_tokens, temperature=0.3):
        items = {int(m.group(1)): m.group(2) for m in re.finditer(r"^(\d+): (.+?)  \[", prompt, re.M)}
        groups = {"Renters and apartments": [], "Uneven floors and gaps": [], "Carpet": [], "Questions": [], "Core": []}
        for i, k in items.items():
            if re.search(r"renter|apartment|without tools", k):
                groups["Renters and apartments"].append(i)
            elif re.search(r"uneven", k):
                groups["Uneven floors and gaps"].append(i)
            elif "carpet" in k:
                groups["Carpet"].append(i)
            elif k.split()[0] in ("how", "does"):
                groups["Questions"].append(i)
            else:
                groups["Core"].append(i)
        clusters = [{"name": n, "primary_id": ids[0], "keyword_ids": ids, "intent": "commercial", "angle": f"angle for {n}"}
                    for n, ids in groups.items() if ids]
        if bad:
            clusters[0]["keyword_ids"] = clusters[0]["keyword_ids"][:-1] + [9999]    # unknown id; one keyword forgotten
        return json.dumps({"clusters": clusters})
    return fake


def run_main(fake=None, pages=None, cfg=None, season=None, manual_lines=None, real_autocomplete=False):
    out = Path(tempfile.mkdtemp())
    if manual_lines is not None:
        (out / "manual_keywords.txt").write_text("\n".join(manual_lines))
    saved = (llm.chat, kw.autocomplete, kw.seasonality, main.PAUSE, main.ROOT)
    llm.chat = fake or fake_llm_factory()
    if not real_autocomplete:
        kw.autocomplete = fake_ac
    if season:
        kw.seasonality = season
    main.PAUSE = 0
    main.ROOT = out                                   # keep run_summary.json out of the repo
    try:
        c = cfg or main.load_config()
        c["limits"]["depth2_seeds"] = 3
        c["limits"]["pause_seconds"] = 0
        s = main.run(c, pages=[] if pages is None else pages, out_base=out, today="2026-10-23")
    finally:
        llm.chat, kw.autocomplete, kw.seasonality, main.PAUSE, main.ROOT = saved
    return s, out


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_end_to_end_with_llm():
    pages = [{"url": "https://s.com/renters", "title": "Draft stoppers for renters",
              "focus_keyword": "door draft stopper for renters"}]
    s, out = run_main(pages=pages)
    assert s["status"] == "success" and s["clustered_by"].startswith("Groq")
    base = out / "under-door-draft-stoppers"
    for f in ("keyword_plan.md", "keywords.csv", "clusters.csv", "content_plan.csv", "research_queue.csv"):
        assert (base / f).exists(), f
    plan_rows = _read(base / "content_plan.csv")
    statuses = [r["coverage"] for r in plan_rows]
    assert "covered" in statuses and statuses[-1] == "covered"                 # covered topics sort last
    queue = _read(base / "research_queue.csv")
    assert queue and all(q["focus_keyword"] not in [r["primary_keyword"] for r in plan_rows if r["coverage"] == "covered"] for q in queue)
    kws = _read(base / "keywords.csv")
    assert {"keyword", "intent", "source", "search_volume", "difficulty", "cluster", "role"} <= set(kws[0])
    assert sum(1 for k in kws if k["role"] == "primary") == len(plan_rows)    # exactly one primary per article
    assert all(k["search_volume"] == "Not verified" for k in kws)
    rep = (base / "keyword_plan.md").read_text()
    assert "Not verified" in rep and "Publish order" in rep and "not a traffic or revenue forecast" in rep
    q = [r for r in plan_rows if r["cluster"] == "Questions"][0]
    assert q["intent"] == "informational"            # the fake model said "commercial"; the keywords say otherwise
    assert float([r for r in plan_rows if r["cluster"] == "Carpet"][0]["priority"]) > float(q["priority"])
    # one primary keyword per article, no two plan rows with the same primary
    prim = [r["primary_keyword"] for r in plan_rows]
    assert len(prim) == len(set(prim))


def test_llm_garbage_is_repaired():
    s, out = run_main(fake=fake_llm_factory(bad=True))
    assert s["status"] == "success"
    kws = _read(out / "under-door-draft-stoppers" / "keywords.csv")
    assert all(k["cluster"] for k in kws)                                       # every keyword landed in a cluster


def test_fallback_without_llm():
    def no_key(*a, **k):
        raise llm.LLMUnavailable("GROQ_API_KEY is not set")
    s, out = run_main(fake=no_key)
    assert s["status"] == "success" and s["clustered_by"] == "rule-based fallback"
    assert any("rule-based" in n for n in s["notes"])
    assert (out / "under-door-draft-stoppers" / "content_plan.csv").exists()


def test_no_pages_means_all_gaps_with_hint():
    s, out = run_main()
    rep = (out / "under-door-draft-stoppers" / "keyword_plan.md").read_text()
    assert "No pages listed" in rep
    assert all(r["coverage"] == "gap" for r in _read(out / "under-door-draft-stoppers" / "content_plan.csv"))


def test_per_cluster_trends_only_when_trends_works():
    calls = []

    def verified(keyword, declared):
        calls.append(keyword)
        return {"status": "verified", "peak_months": [11, 12], "index_by_month": {}, "note": "trends"}
    s, out = run_main(season=verified)
    assert 1 < len(calls) <= 1 + main.load_config()["limits"]["trends_clusters"]
    rows_ = _read(out / "under-door-draft-stoppers" / "content_plan.csv")
    assert all(r["season_status"] == "verified" for r in rows_[:1])

    calls.clear()

    def blocked(keyword, declared):
        calls.append(keyword)
        return {"status": "user_declared", "peak_months": [10, 11], "index_by_month": {}, "note": "declared"}
    s, out = run_main(season=blocked)
    assert len(calls) == 1                                                       # no point hammering a blocked service
    assert all(r["season_status"] == "user_declared" for r in _read(out / "under-door-draft-stoppers" / "content_plan.csv"))


def test_too_few_keywords_fails_cleanly():
    out = Path(tempfile.mkdtemp())
    saved, saved_root = kw.autocomplete, main.ROOT
    kw.autocomplete = lambda q, source="google", stats=None, timeout=10: []
    main.ROOT = out
    try:
        c = main.load_config(saved_root / "config.yaml")
        c["limits"]["pause_seconds"] = 0
        s = main.run(c, pages=[], out_base=out, today="2026-10-23")
    finally:
        kw.autocomplete, main.ROOT = saved, saved_root
    assert s["status"].startswith("failed")


# ------------------------------------------------------------------ search engine failures (the real-world problem)
class FakeResp:
    def __init__(self, status=200, body=b"[]"):
        self.status_code, self.content = status, body


def fake_http(behaviour, calls):
    """behaviour: {host fragment: (status, body bytes)}"""
    def get(url, params=None, timeout=10, headers=None):
        calls.append(url)
        for frag, (status, body) in behaviour.items():
            if frag in url:
                return FakeResp(status, body)
        return FakeResp(404, b"")
    return get


def with_http(behaviour, fn):
    calls, saved = [], kw.requests.get
    kw.requests.get = fake_http(behaviour, calls)
    try:
        return fn(calls)
    finally:
        kw.requests.get = saved


def test_parse_every_engine_format():
    assert kw.parse_suggestions(["q", ["door draft stopper", "x"], []]) == ["door draft stopper", "x"]
    assert kw.parse_suggestions([{"phrase": "door draft stopper"}]) == ["door draft stopper"]
    assert kw.parse_suggestions({"suggestions": [{"value": "door draft stopper", "type": "KEYWORD"}]}) == ["door draft stopper"]
    assert kw.parse_suggestions({}) == [] and kw.parse_suggestions("nonsense") == []


def test_blocked_engine_is_reported_and_given_up_quickly():
    def go(calls):
        stats = kw.Stats(["google"])
        for i in range(20):
            kw.autocomplete(f"door draft stopper {i}", "google", stats)
        return stats, calls
    stats, calls = with_http({"google.com": (429, b"Too Many Requests")}, go)
    st = stats.s["google"]
    assert st["disabled"] and st["errors"] == kw.BREAKER and st["last_error"] == "HTTP 429"
    assert len(calls) == kw.BREAKER                                       # it stopped hammering the blocked engine
    assert "given up" in stats.lines()[0] and "HTTP 429" in stats.lines()[0]


def test_html_block_page_is_named_as_such():
    stats = kw.Stats(["google"])
    with_http({"google.com": (200, b"<html>unusual traffic</html>")}, lambda c: kw.autocomplete("door draft", "google", stats))
    assert "not JSON" in stats.s["google"]["last_error"]


def test_other_engines_carry_the_run_when_google_is_blocked():
    bing = json.dumps(["door draft stopper", ["door draft stopper", "door draft stopper for renters", "best door draft stopper",
                                              "door draft stopper for carpet", "door draft stopper without tools", "door draft stopper vs door sweep"]]).encode()
    behaviour = {"suggestqueries.google.com": (403, b"blocked"), "api.bing.com": (200, bing),
                 "duckduckgo.com": (200, b"[]"), "completion.amazon.com": (500, b"")}

    def go(calls):
        return kw.collect(SEEDS, max_total=90, depth=2, depth2_seeds=3, pause=0, sources=["google", "bing", "duckduckgo", "amazon"])
    rows_, stats = with_http(behaviour, go)
    assert len(rows_) >= 5 and {r["source"].split("_")[0] for r in rows_} == {"bing"}
    assert stats.s["google"]["disabled"] and stats.s["amazon"]["disabled"]
    assert stats.s["bing"]["with_results"] > 0 and stats.s["duckduckgo"]["empty"] > 0 and not stats.s["duckduckgo"]["disabled"]


def test_all_engines_blocked_gives_a_useful_message_and_diagnostics():
    behaviour = {"google.com": (429, b""), "bing.com": (403, b""), "duckduckgo.com": (403, b""), "amazon.com": (503, b"")}
    saved = kw.requests.get
    kw.requests.get = fake_http(behaviour, [])
    out = Path(tempfile.mkdtemp())
    saved_root, saved_ac = main.ROOT, kw.autocomplete
    main.ROOT = out
    try:
        c = main.load_config(saved_root / "config.yaml")
        c["limits"]["pause_seconds"] = 0
        s = main.run(c, pages=[], out_base=out, today="2026-10-23")
    finally:
        kw.requests.get, main.ROOT = saved, saved_root
    assert s["status"].startswith("failed: only 0 keyword(s) found")
    detail = " ".join(s["details"])
    assert "HTTP 429" in detail and "HTTP 403" in detail and "HTTP 503" in detail
    saved_json = json.loads((out / "run_summary.json").read_text())
    assert saved_json["diagnostics"]["google"]["last_error"] == "HTTP 429"        # the log artifact says WHY


def test_manual_keywords_rescue_a_fully_blocked_run():
    lines = ["# pasted from the Google search box", "door draft stopper for renters", "best door draft stopper", "door draft stopper for carpet",
             "door draft stopper without tools", "how to stop a draft under a door", "door draft stopper vs door sweep"]
    behaviour = {"google.com": (429, b""), "bing.com": (403, b""), "duckduckgo.com": (403, b""), "amazon.com": (503, b"")}
    saved = kw.requests.get
    kw.requests.get = fake_http(behaviour, [])
    try:
        s, out = run_main(manual_lines=lines, fake=fake_llm_factory(), real_autocomplete=True)
    finally:
        kw.requests.get = saved
    assert s["status"] == "success" and s["keywords"] == 6
    assert any("manual_keywords.txt" in n for n in s["notes"])


def test_keywords_csv_is_readable_by_the_seo_agent():
    seo = ROOT.parent / "amazon-seo-agent"
    if not seo.exists():
        print("  (skipped: SEO agent folder not next to this one)")
        return
    s, out = run_main()
    path = out / "under-door-draft-stoppers" / "keywords.csv"
    code = ("import main, sys; from pathlib import Path; "
            "pool = main.load_keyword_pool(Path(sys.argv[1])); print(len(pool), pool[0])")
    r = subprocess.run([sys.executable, "-c", code, str(path)], cwd=seo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert int(r.stdout.split()[0]) > 5


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
