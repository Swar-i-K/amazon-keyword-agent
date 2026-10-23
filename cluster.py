"""
cluster.py
----------
Groups keywords into topic clusters, each meant to become ONE article with ONE primary keyword.

Two ways, same output shape:
  * cluster_with_llm(): Groq groups by meaning and names each cluster. The answer is validated by Python:
    every keyword ends up in exactly one cluster, the primary keyword must be a real member, and
    anything the model forgot is assigned to the closest cluster. The model can never add a keyword.
  * cluster_deterministic(): no key needed. Groups by the most common "modifier" word
    (what is left after removing the seed topic words and intent words), e.g. renters, apartment, uneven.
"""
from __future__ import annotations

import re
from collections import Counter

import keywords as kw
import llm

INTENT_RANK = {"transactional": 0, "commercial": 0, "informational": 1, "unclear": 2}
ALLOWED_INTENT = ("commercial", "transactional", "informational", "unclear")


def _rows_by_kw(rows: list) -> dict:
    return {r["keyword"]: r for r in rows}


def pick_primary(members: list, by_kw: dict, key_word: str = "") -> str:
    """Prefer money intent, then keywords containing the cluster's key word, then shorter phrases."""
    def score(k):
        r = by_kw[k]
        has_key = 0 if (key_word and key_word in kw.content_words(k)) else 1
        return (INTENT_RANK.get(r["intent"], 2), has_key, len(k.split()), k)
    return sorted(members, key=score)[0]


def majority_intent(members: list, by_kw: dict) -> str:
    c = Counter(by_kw[k]["intent"] for k in members)
    money = c.get("commercial", 0) + c.get("transactional", 0)
    if money and money >= c.get("informational", 0):
        return "commercial" if c.get("commercial", 0) >= c.get("transactional", 0) else "transactional"
    return c.most_common(1)[0][0]


def _finish(groups: dict, rows: list, names: dict = None, meta: dict = None, max_clusters: int = 12) -> list:
    """groups: {label: [keywords]} -> list of cluster dicts, largest first, capped."""
    by_kw = _rows_by_kw(rows)
    names, meta = names or {}, meta or {}
    out = []
    for label, members in groups.items():
        if not members:
            continue
        m = meta.get(label, {})
        primary = m.get("primary") if m.get("primary") in members else pick_primary(members, by_kw, label)
        intent = majority_intent(members, by_kw)         # from the keywords themselves, never from the model's say-so
        out.append({
            "name": names.get(label) or label.title(),
            "primary": primary,
            "intent": intent,
            "keywords": sorted(members, key=lambda k: (k != primary, len(k.split()), k)),
            "angle": m.get("angle") or "",
            "source": m.get("source", "deterministic"),
        })
    out.sort(key=lambda c: (-len(c["keywords"]), c["name"]))
    if len(out) > max_clusters:                                  # fold the smallest into "More long-tail ideas"
        keep, rest = out[: max_clusters - 1], out[max_clusters - 1:]
        merged = [k for c in rest for k in c["keywords"]]
        keep.append({"name": "More long-tail ideas", "primary": pick_primary(merged, by_kw), "intent": majority_intent(merged, by_kw),
                     "keywords": sorted(merged, key=lambda k: (len(k.split()), k)), "angle": "", "source": "deterministic"})
        out = keep
    return out


# ------------------------------------------------------------------ deterministic
def cluster_deterministic(rows: list, seeds: list, max_clusters: int = 12) -> list:
    seed_words = kw.seed_word_set(seeds)
    mods = {r["keyword"]: [w for w in kw.content_words(r["keyword"], drop=seed_words)] for r in rows}
    freq = Counter(w for ms in mods.values() for w in set(ms))
    groups = {}
    for r in rows:
        ms = mods[r["keyword"]]
        label = "core" if not ms else sorted(ms, key=lambda w: (-freq[w], w))[0]
        groups.setdefault(label, []).append(r["keyword"])
    # singletons (a modifier seen once) carry no pattern: fold them into one long-tail group
    tail = [k for lab, ks in list(groups.items()) if lab != "core" and len(ks) < 2 for k in ks]
    for lab in [lab for lab, ks in groups.items() if lab != "core" and len(ks) < 2]:
        groups.pop(lab)
    if tail:
        groups["more long-tail ideas"] = tail
    names = {"core": "Core: the main topic and 'best' searches"}
    return _finish(groups, rows, names=names, max_clusters=max_clusters)


# ------------------------------------------------------------------ LLM
def build_prompt(rows: list, niche: str, max_clusters: int) -> str:
    lines = "\n".join(f"{i}: {r['keyword']}  [{r['intent']}]" for i, r in enumerate(rows))
    return f"""You are an SEO strategist for an affiliate site about: {niche}.
Group the numbered keywords below into topic clusters. Each cluster will become ONE article, so every keyword in a cluster
must be answerable by the same page (same searcher need). Different needs (for example renters vs uneven floors vs cheap)
belong in different clusters.

KEYWORDS (id: keyword [intent]):
{lines}

Return ONE JSON object and nothing else:
{{
  "clusters": [
    {{"name": "<3-6 words>", "primary_id": <id of the best focus keyword, chosen FROM this cluster>,
      "keyword_ids": [<ids>], "intent": "commercial|transactional|informational",
      "angle": "<one sentence: what the article should help the reader decide or fix>"}}
  ]
}}

RULES
- Use each id exactly once. Do not invent keywords or ids.
- At most {max_clusters} clusters, each with at least 2 keywords where possible.
- The primary keyword should be the broadest natural phrase that fits all the others and has buying or comparison intent when available.
- Do not mention search volumes, rankings or competition.
"""


def cluster_with_llm(rows: list, niche: str, cfg: dict) -> list:
    max_clusters = cfg["limits"].get("max_clusters", 12)
    model, mx = cfg["llm"]["model"], cfg["llm"].get("max_tokens", 5000)
    last = None
    for _ in range(2):
        try:
            raw = llm.extract_json(llm.chat(build_prompt(rows, niche, max_clusters), model, mx, 0.2))
            return validate_clusters(raw, rows, max_clusters)
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"clustering failed after retry: {last}")


def validate_clusters(raw: dict, rows: list, max_clusters: int = 12) -> list:
    n = len(rows)
    assigned, groups, names, meta = {}, {}, {}, {}
    for i, c in enumerate(raw.get("clusters") or []):
        ids = []
        for x in c.get("keyword_ids") or []:
            try:
                x = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= x < n and x not in assigned:           # unknown ids dropped; an id can only be used once
                assigned[x] = i
                ids.append(x)
        if not ids:
            continue
        label = f"c{i}"
        groups[label] = [rows[x]["keyword"] for x in ids]
        names[label] = re.sub(r"\s+", " ", str(c.get("name") or "")).strip()[:60] or None
        try:
            pid = int(c.get("primary_id"))
        except (TypeError, ValueError):
            pid = -1
        meta[label] = {"primary": rows[pid]["keyword"] if pid in ids else None,
                       "intent": c.get("intent"), "angle": re.sub(r"\s+", " ", str(c.get("angle") or "")).strip()[:240],
                       "source": "llm"}
    if not groups:
        raise ValueError("model returned no usable clusters")

    # anything the model forgot goes to the most similar cluster (never dropped)
    for x in range(n):
        if x in assigned:
            continue
        words = set(kw.content_words(rows[x]["keyword"]))
        best = max(groups, key=lambda lab: (len(words & {w for k in groups[lab] for w in kw.content_words(k)}),
                                            len(groups[lab])))
        groups[best].append(rows[x]["keyword"])
        assigned[x] = best
    return _finish(groups, rows, names=names, meta=meta, max_clusters=max_clusters)
