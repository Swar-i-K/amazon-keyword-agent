"""
Offline tests for ideas.py and providers.py: no network, no API key.   Run:  python tests/test_ideas.py
HTTP calls and the Groq model are faked, so these prove parsing, validation, provider fallback and export,
not the quality of real Gemini, Perplexity or Groq answers.
"""
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ideas  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import providers  # noqa: E402

KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "PERPLEXITY_API_KEY", "IDEAS_PRODUCTS")


def P(name, **kw):
    return ideas.make_product(name, **kw)


PRODUCTS = [
    P("Twin Draft Guard", asin="B08P29T3WW", category="draft stoppers", seed_keyword="Door Draft Stopper", price="$14.99", rating="4.6", review_count="12000", features=["fits most doors"]),
    P("Slim Door Sweep", asin="B0018UI4T6", category="draft stoppers"),
]


def ideas_json(platform):
    return json.dumps({"ideas": [
        {"angle": "buyer_intent", "title": f"Best draft stoppers ({platform})", "problem": "Which one to buy?", "product_ids": [0, 1], "seed_phrase": "Best Draft Stopper!"},
        {"angle": "problem_solving", "title": f"How to stop cold air under a door ({platform})", "problem": "Cold floor", "product_ids": [7, "x"], "seed_phrase": "x"},
        {"angle": "weird", "title": f"best draft stoppers ({platform})", "problem": "duplicate of the first", "product_ids": [], "seed_phrase": "best draft stopper"},
        {"angle": "product_specific", "title": "We tested Twin Draft Guard for 30 days", "problem": "review", "product_ids": [0], "seed_phrase": "twin draft guard review"},
        {"angle": "comparison", "title": f"Twin Draft Guard vs Slim Door Sweep ({platform})", "problem": "Which for whom", "product_ids": [0, 1], "seed_phrase": "draft guard vs door sweep"},
    ]})


def fake_groq(prompt, model, max_tokens, temperature=0.3):
    return ideas_json(re.search(r"Platform: (.+)", prompt).group(1).strip())


class FakeResp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text, self.headers = status, body, text or json.dumps(body or {}), headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def gemini_ok(prompt):
    plat = re.search(r"Platform: (.+)", prompt).group(1).strip()
    return FakeResp(200, {"choices": [{"message": {"content": ideas_json(plat)}}]})


class Env:
    """Sets API keys + fake network/model for one test, restores everything afterwards."""
    def __init__(self, keys=(), post=None, groq=fake_groq):
        self.keys, self.post, self.groq = keys, post, groq
        self.calls = []

    def __enter__(self):
        self.saved = ({k: os.environ.pop(k, None) for k in KEYS}, llm.chat, providers.requests.post, providers.time.sleep)
        for k in self.keys:
            os.environ[k] = "test-key"
        llm.chat = self.groq

        def post(url, headers=None, json=None, timeout=None):
            self.calls.append((url, json))
            return self.post(url, json)
        providers.requests.post = post
        providers.time.sleep = lambda s: None
        return self

    def __exit__(self, *a):
        env, llm.chat, providers.requests.post, providers.time.sleep = self.saved
        for k in KEYS:
            os.environ.pop(k, None)
            if env[k] is not None:
                os.environ[k] = env[k]


def run_ideas(products=PRODUCTS, cfg=None):
    out = Path(tempfile.mkdtemp())
    saved_root = ideas.ROOT
    ideas.ROOT = out
    try:
        c = cfg or main.load_config()
        c["ideas"] = {**(c.get("ideas") or {}), "per_platform": 4}
        s = ideas.run(c, products=products, out_base=out, today="2026-10-30")
    finally:
        ideas.ROOT = saved_root
    return s, out


def _read(path):
    """Reads the Ideas sheet of content_ideas.xlsx (the old csv path picks the folder)."""
    from openpyxl import load_workbook
    rows = list(load_workbook(path.parent / "content_ideas.xlsx")["Ideas"].iter_rows(values_only=True))
    return [dict(zip(rows[0], ["" if v is None else v for v in r])) for r in rows[1:]]


def _no_http(url, body):
    raise AssertionError(f"unexpected HTTP call to {url}")


# ------------------------------------------------------------------ products
def test_make_product_asin_and_link():
    a = ideas.make_product("A", asin="b08p29t3ww")
    assert a["asin"] == "B08P29T3WW" and a["link"] == "https://www.amazon.com/dp/B08P29T3WW"
    b = ideas.make_product("B", link="https://www.amazon.com/Some-Name/dp/B0018UI4T6/ref=sr_1_1?x=1")
    assert b["asin"] == "B0018UI4T6" and b["link"].startswith("https://www.amazon.com/Some-Name")
    c = ideas.make_product("C", asin="not-an-asin")
    assert c["asin"] == "" and c["link"] == ""                       # a bad ASIN is dropped, never guessed
    assert ideas.make_product("D", seed_keyword="  Door Draft STOPPER ")["seed_keyword"] == "door draft stopper"


def test_load_products_columns_aliases_and_env():
    d = Path(tempfile.mkdtemp())
    p = d / "products.csv"
    p.write_text("Name,ASIN,Amz Link,Category,Seed Keyword\n"
                 "Alpha,B08P29T3WW,,Draft Stoppers,door draft stopper\n"
                 ",,,,\n"
                 "Beta,,https://www.amazon.com/dp/B0018UI4T6,Draft Stoppers,\n"
                 "Gamma,,,Weather Stripping,weather stripping\n", encoding="utf-8")
    got = ideas.load_products(p)
    assert [x["name"] for x in got] == ["Alpha", "Beta", "Gamma"]
    assert got[0]["link"].endswith("B08P29T3WW") and got[1]["asin"] == "B0018UI4T6" and got[2]["category"] == "Weather Stripping"
    assert ideas.load_products(d / "missing.csv") == []
    assert [x["name"] for x in ideas.products_from_env("A; B ;; C")] == ["A", "B", "C"]
    old = d / "old.csv"                                                # the earlier column layout still loads
    old.write_text("name,price,rating,review_count,features,notes\nOld,$5,4.5,100,a;b,note\n", encoding="utf-8")
    assert ideas.load_products(old)[0]["features"] == ["a", "b"]


def test_group_by_category():
    ps = [P("a", category="X"), P("b", category="Y"), P("c"), P("d", category="X"), P("e", category="Z")]
    groups, notes = ideas.group_by_category(ps, "niche", max_categories=3, max_products=1)
    assert [g[0] for g in groups] == ["X", "Y", "niche"]               # no category -> the config niche
    assert all(len(g[1]) <= 1 for g in groups)
    assert any("categories" in n for n in notes) and any("products were used" in n for n in notes)
    assert ideas.group_by_category([], "niche", 3, 6)[0] == [("niche", [])]


# ------------------------------------------------------------------ validation
def test_validate_guards():
    raw = json.loads(ideas_json("WordPress article"))
    got, dropped = ideas.validate_ideas(raw, "article", PRODUCTS, 10)
    titles = [i["title"].lower() for i in got]
    assert dropped == 2 and not any("tested" in t for t in titles) and len(titles) == len(set(titles))
    by = {i["title"]: i for i in got}
    first = by["Best draft stoppers (WordPress article)"]
    assert first["products"] == ["Twin Draft Guard", "Slim Door Sweep"] and len(first["links"]) == 2
    assert first["seed_phrase"] == "best draft stopper"
    second = by["How to stop cold air under a door (WordPress article)"]
    assert second["products"] == [] and 2 <= len(second["seed_phrase"].split()) <= 6   # garbage ids dropped; bad seed rebuilt
    assert all(i["angle"] in ideas.ANGLES + ("other",) for i in got)


def test_hands_on_flag_and_cap():
    raw = {"ideas": [{"angle": "product_specific", "title": "Hands-on with Twin Draft Guard", "problem": "", "product_ids": [0], "seed_phrase": "twin draft guard"}]}
    assert ideas.validate_ideas(raw, "article", PRODUCTS, 5, hands_on=False)[0] == []
    assert len(ideas.validate_ideas(raw, "article", PRODUCTS, 5, hands_on=True)[0]) == 1
    many = {"ideas": [{"angle": "buyer_intent", "title": f"Idea number {i}", "problem": "", "product_ids": [], "seed_phrase": f"idea {i}"} for i in range(30)]}
    assert len(ideas.validate_ideas(many, "article", PRODUCTS, 5)[0]) == 5


def test_prompt_content():
    p = ideas.build_prompt("youtube", PRODUCTS, "draft stoppers", 6, [10, 11], "- do draft stoppers work on carpet?")
    assert "Twin Draft Guard" in p and "$14.99" in p and "Oct, Nov" in p and "owner's seed keyword: door draft stopper" in p
    assert "Never invent" in p and "TWO OR MORE products" in p and "MARKET NOTES" in p and "carpet" in p
    assert "B08P29T3WW" not in p and "amazon.com" not in p            # the model never sees links, so it cannot invent them
    one = ideas.build_prompt("youtube", PRODUCTS[:1], "draft stoppers", 6, [])
    assert "TWO OR MORE" not in one and "MARKET NOTES" not in one


# ------------------------------------------------------------------ end to end
def test_end_to_end_groq_only():
    with Env(keys=["GROQ_API_KEY"], post=_no_http):
        s, out = run_ideas()
    assert s["status"] == "success" and len(s["categories"]) == 1
    cat = s["categories"][0]
    assert cat["answered_by"] == {"groq": 4} and cat["generated_by"] == "AI (validated)"
    base = out / "draft-stoppers"
    rows = _read(base / "content_ideas.csv")
    assert {r["platform"] for r in rows} == {"WordPress article", "YouTube video", "Facebook/Instagram post", "Pinterest pin"}
    assert {"platform", "angle", "title", "problem", "products", "product_links", "seed_phrase", "source"} <= set(rows[0])
    assert any("amazon.com/dp/B08P29T3WW" in r["product_links"] for r in rows)
    md = (base / "content_ideas.md").read_text(encoding="utf-8")
    assert "not validated demand" in md and "[Twin Draft Guard](https://www.amazon.com/dp/B08P29T3WW)" in md
    seeds = (base / "seed_phrases.txt").read_text().splitlines()
    assert seeds[0] == "door draft stopper" and len(seeds) == len(set(seeds))      # your own seed keyword comes first
    assert json.loads((out / "ideas_run_summary.json").read_text())["ideas"] == len(rows)


def test_two_categories_two_folders():
    ps = [P("Alpha", category="Draft Stoppers"), P("Beta", category="Weather Stripping")]
    with Env(keys=["GROQ_API_KEY"], post=_no_http):
        s, out = run_ideas(products=ps)
    assert [c["category"] for c in s["categories"]] == ["Draft Stoppers", "Weather Stripping"]
    assert (out / "draft-stoppers" / "content_ideas.md").exists() and (out / "weather-stripping" / "content_ideas.md").exists()


def test_no_products_still_works_and_says_so():
    with Env(keys=["GROQ_API_KEY"], post=_no_http):
        s, out = run_ideas(products=[])
    assert s["status"] == "success" and any("category only" in n for n in s["notes"])


# ------------------------------------------------------------------ providers
def test_gemini_first_and_retired_model_name_is_skipped():
    seen = []

    def post(url, body):
        assert "generativelanguage.googleapis.com" in url
        seen.append(body["model"])
        if body["model"] == providers.DEFAULT_GEMINI_MODELS[0]:
            return FakeResp(404, text="model not found")
        return gemini_ok(body["messages"][0]["content"])
    with Env(keys=["GEMINI_API_KEY", "GROQ_API_KEY"], post=post, groq=_no_http):
        s, out = run_ideas()
    assert s["categories"][0]["answered_by"] == {"gemini": 4}
    assert seen[0] == "gemini-3.5-flash" and seen[1] == "gemini-2.5-flash"      # the dead name was tried once, then the next one


def test_gemini_rate_limit_then_groq():
    def post(url, body):
        return FakeResp(429, text="quota", headers={"retry-after": "1"})
    with Env(keys=["GEMINI_API_KEY", "GROQ_API_KEY"], post=post):
        s, out = run_ideas()
    assert s["categories"][0]["answered_by"] == {"groq": 4}


def test_gemini_bad_key_then_groq():
    with Env(keys=["GEMINI_API_KEY", "GROQ_API_KEY"], post=lambda u, b: FakeResp(403, text="API key not valid")):
        s, out = run_ideas()
    assert s["categories"][0]["answered_by"] == {"groq": 4}


def test_unusable_gemini_answer_goes_to_groq_on_retry():
    with Env(keys=["GEMINI_API_KEY", "GROQ_API_KEY"],
             post=lambda u, b: FakeResp(200, {"choices": [{"message": {"content": "sorry, no json"}}]})):
        s, out = run_ideas()
    assert s["categories"][0]["answered_by"] == {"groq": 4}


def test_groq_only_or_gemini_only_and_provider_order():
    with Env(keys=["GEMINI_API_KEY"], post=lambda u, b: gemini_ok(b["messages"][0]["content"]), groq=_no_http):
        s, out = run_ideas()
    assert s["categories"][0]["answered_by"] == {"gemini": 4}
    cfg = main.load_config()
    cfg["ideas"] = {"providers": ["groq", "gemini"]}
    with Env(keys=["GEMINI_API_KEY", "GROQ_API_KEY"], post=_no_http):
        s, out = run_ideas(cfg=cfg)
    assert s["categories"][0]["answered_by"] == {"groq": 4}


def test_no_keys_uses_templates():
    with Env(keys=[], post=_no_http, groq=_no_http):
        s, out = run_ideas()
    cat = s["categories"][0]
    assert cat["generated_by"] == "template" and any("No AI key" in n for n in cat["notes"])
    rows = _read(out / "draft-stoppers" / "content_ideas.csv")
    assert rows and all(r["source"] == "template" for r in rows)


def test_perplexity_research_feeds_the_prompt_and_strips_citations():
    prompts = []

    def post(url, body):
        if "perplexity.ai" in url:
            assert url.endswith("/v1/agent") and body["effort"] == "fast" and body["tools"] == [{"type": "web_search"}]
            return FakeResp(200, {"output": [{"type": "search_results", "results": []},
                                             {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "- Do draft stoppers work on carpet? [1]\n- Renters worry about tools [web:2]"}]}]})
        prompts.append(body["messages"][0]["content"])
        return gemini_ok(body["messages"][0]["content"])
    with Env(keys=["PERPLEXITY_API_KEY", "GEMINI_API_KEY"], post=post):
        s, out = run_ideas()
    cat = s["categories"][0]
    assert cat["answered_by"]["perplexity"] == 1 and cat["answered_by"]["gemini"] == 4
    assert prompts and all("MARKET NOTES" in p and "work on carpet?" in p for p in prompts)
    assert "[1]" not in prompts[0] and "[web:2]" not in prompts[0]
    md = (out / "draft-stoppers" / "content_ideas.md").read_text(encoding="utf-8")
    assert "Shopper research" in md and "carpet" in md


def test_perplexity_failure_does_not_stop_the_run():
    def post(url, body):
        if "perplexity.ai" in url:
            return FakeResp(402, text="insufficient credits")
        return gemini_ok(body["messages"][0]["content"])
    with Env(keys=["PERPLEXITY_API_KEY", "GEMINI_API_KEY"], post=post):
        s, out = run_ideas()
    cat = s["categories"][0]
    assert "perplexity" not in cat["answered_by"] and cat["generated_by"] == "AI (validated)"
    assert any("Perplexity research skipped" in n for n in cat["notes"])


def test_research_off_or_no_key_makes_no_perplexity_call():
    def post(url, body):
        assert "perplexity.ai" not in url
        return gemini_ok(body["messages"][0]["content"])
    with Env(keys=["GEMINI_API_KEY"], post=post):                       # no Perplexity key
        run_ideas()
    cfg = main.load_config()
    cfg["ideas"] = {"research": False}
    with Env(keys=["GEMINI_API_KEY", "PERPLEXITY_API_KEY"], post=post):  # key set but research switched off
        run_ideas(cfg=cfg)


def test_agent_text_shapes():
    assert providers._agent_text({"output_text": "hello"}) == "hello"
    assert providers._agent_text({"output": [{"type": "message", "content": "plain"}]}) == "plain"
    assert providers._agent_text({"output": [{"type": "message", "content": [{"type": "output_text", "text": "a"}, {"type": "output_text", "text": "b"}]}]}) == "a\nb"
    assert providers._agent_text({"output": []}) == ""


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
