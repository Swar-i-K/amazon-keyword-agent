# Keyword Agent ($0)

Turns one or two seed phrases into a **keyword-to-article plan**: which keyword each article should target, which topics you have not
covered yet, and what to publish first. Free: Google autocomplete + Google Trends + Groq. No paid SEO tool.

## What it does
1. Expands each seed with Google autocomplete (question words, "for / vs / without / with", A-Z, then a second pass on the best finds).
2. Groups the keywords into clusters. **One cluster = one article = one primary keyword**, so your articles stop competing with each other.
   (Groq groups by meaning and Python validates it; with no API key a rule-based fallback groups by the most common modifier word.)
3. Compares the clusters with your existing posts in `site_pages.csv`: **covered / partial / gap**, and warns when two of your posts
   target nearly the same keyword.
4. Checks seasonality on Google Trends (falls back to the months you declared in `config.yaml`, labelled `user_declared`).
5. Ranks everything into a publish order and writes the files below.

## Files (`output/<niche>/`)
| File | Use |
|---|---|
| `keyword_plan.md` | Publish order, clusters with supporting keywords, gaps, cannibalization, question searches (FAQ ideas) |
| `content_plan.csv` | One row per planned article, in publish order |
| `research_queue.csv` | Gap topics to feed the **research agent** (`search_terms`) and then the article agent |
| `keywords.csv` | Every keyword with its cluster and role. The **SEO agent** reads it (`keyword` column) |
| `clusters.csv` | One row per cluster |

## Setup (once)
1. Put the files in the root of a GitHub repo (public is fine: nothing private is in here unless you list unpublished URLs).
2. Add the secret `GROQ_API_KEY` (same key as your other agents). Without it the agent still runs, with plainer clusters.
3. Edit `config.yaml`: niche, 1-3 seeds, `declared_peak_months`.
4. Fill `site_pages.csv` (`url,title,focus_keyword`) with your published posts. Without it every cluster counts as a gap.

## Run
Actions -> **Keyword Agent** -> Run workflow (the two boxes optionally override the seeds and niche). Download **keyword-plan** and open `keyword_plan.md`.

## How it connects to your other agents
- Research agent: take the top row(s) of `research_queue.csv` and put the `search_terms` into the research workflow's search_terms box.
  (The research agent does not read `keywords.csv`.)
- SEO agent: copy `keywords.csv` into its repo; use the cluster's primary keyword as `focus_keyword` in its `jobs.csv`.
- After you publish an article, add it to `site_pages.csv` with its focus keyword and rerun, so the plan stays current.

## Honest limits
- **No search volume and no keyword difficulty.** Those need a paid tool and read "Not verified". Autocomplete shows what people type, not how often.
- **Priority is a screening order, not a traffic or revenue forecast.** It blends intent (money intent first), gap, breadth (how many distinct
  searches Google suggests, a weak demand proxy) and season. Components with no data are dropped, not guessed.
- Cluster intent is computed from the keywords themselves, not taken from the model.
- Google Trends is unofficial and is often blocked from cloud IPs. The agent then uses your declared months and says so; it only tries
  per-cluster Trends calls when the first call works.
- It cannot see what currently ranks on Google, so it cannot tell you how hard a keyword is.
- Coverage matching is word-based. It can miss a page that covers an angle with different wording, so review "gap" rows before assuming nothing exists.
- Google may rate-limit autocomplete. If fewer than 5 keywords come back the run stops with a clear message instead of making up a plan.

## Tests
`python tests/test_offline.py` (no network or API key). Autocomplete and the model are faked, so they prove the logic, not the quality of real Google or Groq results.
The first real run is the live test.
