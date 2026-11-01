# Keyword Agent ($0)

Turns one or two seed phrases into a **keyword-to-article plan**: which keyword each article should target, which topics you have not
covered yet, and what to publish first. Free: Google autocomplete + Google Trends + Groq. No paid SEO tool.

## What it does
1. Expands each seed with the free suggestion boxes of **Google, Bing, DuckDuckGo and Amazon** (question words, "for / vs / without / with", A-Z,
   then a second pass on the best finds). If one engine blocks the run, the others still work; a blocked engine is given up after 4 failed
   requests and the log says why (for example `HTTP 429`).
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
| `keyword_plan.xlsx` > **Content Plan** | One row per planned article, in publish order |
| `keyword_plan.xlsx` > **Research Queue** | Gap topics to feed the **research agent** (`search_terms`) and then the article agent |
| `keyword_plan.xlsx` > **Keywords** | Every keyword with its cluster and role. The **SEO agent** reads it (`keyword` column) |
| `keyword_plan.xlsx` > **Clusters** | One row per cluster |

## Setup (once)
1. Put the files in the root of a GitHub repo (public is fine: nothing private is in here unless you list unpublished URLs).
2. Add the secret `GROQ_API_KEY` (same key as your other agents). Without it the agent still runs, with plainer clusters.
3. Edit `config.yaml`: niche, 1-3 seeds, `declared_peak_months`.
4. Fill `site_pages.csv` (`url,title,focus_keyword`) with your published posts. Without it every cluster counts as a gap.

## If the run says "only 0 keyword(s) found"
The search engines refused the requests (GitHub's servers are sometimes rate-limited). `run_summary.json` in the artifact, and the log, list each engine with its error.
1. Run again later.
2. Or paste keywords yourself: type your topic into Google's or Amazon's search box, copy the suggestions it shows into `manual_keywords.txt` (one per line), run again.
   Manual keywords always count and are never filtered out.
3. Or use a broader seed (2-4 common words).

## Run
Actions -> **Keyword Agent** -> Run workflow (the two boxes optionally override the seeds and niche). Download **keyword-plan** and open `keyword_plan.md`.

## Content ideas (`ideas.py`): different angles from your products
Gives **ideas only** (no articles, scripts or captions) for four places: **WordPress article, YouTube, Facebook/Instagram, Pinterest**.
Each idea has a different angle (buyer intent, problem solving, product specific, comparison, audience, seasonal) and a `seed_phrase`.

1. Fill `products.csv`, one product per row: `name,asin,amazon_link,category,seed_keyword`.
   Only `name` is required. Give the ASIN or the link (the other is filled in; a bad ASIN is ignored, never guessed).
   Products are grouped by `category`; each category gets its own folder of ideas (up to `max_categories`, default 3).
   Optional extra columns: `price,rating,review_count,features` (separate features with `;`), `notes`. Only the facts you give are used.
   Or skip the file and type names in the workflow box, separated by `;`. With no products, ideas use the niche from `config.yaml`.
2. Add the API keys you have as GitHub secrets (Settings -> Secrets and variables -> Actions). Every key is optional:

   | Secret | Used for | Cost |
   |---|---|---|
   | `GEMINI_API_KEY` | writes the ideas (first choice) | Google AI Studio free tier |
   | `GROQ_API_KEY` | writes the ideas if Gemini is missing or fails | free tier |
   | `PERPLEXITY_API_KEY` | web research step: what shoppers ask, fed into the idea prompts | **paid**, pay-as-you-go credits, no free API tier |

   A provider without a key is skipped. With no key at all, plain template ideas are used and labelled `template`.
   Gemini model names are listed in `config.yaml` (`gemini.models`); a retired name (404) falls through to the next one.
   Set `ideas.research: false` to never call Perplexity.
3. Actions -> **Content Ideas** -> Run workflow. Download **content-ideas** and open `output/<category>/content_ideas.md`.
4. To see which ideas people really search for, paste `seed_phrases.txt` into `manual_keywords.txt` (or use 1-3 phrases as seeds) and run the **Keyword Agent**.

Files per category (`output/<category>/`): `content_ideas.md` (grouped by platform and angle, product names link to Amazon), `content_ideas.xlsx`
(sheet **Ideas**, with `product_links`), `seed_phrases.txt` (your own seed keywords first). `ideas_run_summary.json` shows which provider answered each platform.
Limits: ideas are **not checked for demand**; the model never sees Amazon links and cannot add products; with `hands_on_testing: false`
ideas never claim you tested the product. Perplexity's notes are unverified web summaries, used only to decide what to cover.
Tests: `python tests/test_ideas.py`.

## How it connects to your other agents
- Research agent: take the top row(s) of the **Research Queue** sheet and put the `search_terms` into the research workflow's search_terms box.
  (The research agent does not read `keywords.csv`.)
- SEO agent: export the **Keywords** sheet as `keywords.csv` (Excel: File -> Save As -> CSV) and copy it into its repo, or set `output_format: csv`; use the cluster's primary keyword as `focus_keyword` in its `jobs.csv`.
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
- Search engines may rate-limit or block cloud servers. If fewer than 5 keywords come back (including any in `manual_keywords.txt`) the run stops with a per-engine
  explanation instead of making up a plan.
- Amazon's suggestions show what shoppers type, but may include brand names. Review the keyword list before using it.

## Tests
`python tests/test_offline.py` and `python tests/test_ideas.py` (no network or API key). Autocomplete and the model are faked, so they prove the logic, not the quality of real Google or Groq results.
The first real run is the live test.

## Excel or CSV output
All tables come as **Excel** by default: `keyword_plan.xlsx` (sheets Keywords, Clusters, Content Plan, Research Queue) and
`content_ideas.xlsx` (sheet Ideas). Header row is frozen and filterable; numbers stay numbers. Change it in `config.yaml`:
`output_format: xlsx` (default), `csv` (the old separate CSV files) or `both`.
The **SEO agent reads `keywords.csv`**, so for it either export the Keywords sheet to CSV or set `output_format: csv` / `both`.
Inputs stay CSV (`products.csv`, `site_pages.csv`).
