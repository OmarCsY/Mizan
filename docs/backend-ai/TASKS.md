# Backend + AI tasks

Work top to bottom. Each task lists what it depends on, what to build, and the acceptance criteria (AC)
that must all pass before you tick it. `HUMAN` marks a step only the human can do (keys, accounts, sign-off);
when you reach one, stop, print exactly what is needed, and continue with the next task that does not
depend on it.

Spec references (§) point to `docs/backend-ai/SPEC.md`.

Legend: P0 = must ship · P1 = should ship · P2 = if time allows.

---

## Phase A — Foundations

### [x] B01 · Repo skeleton (P0)
Depends on: —
Build:
- Layout from §15 (`backend/`, `bot/`, `scripts/`, `bench/`, `docs/`; leave `frontend/` alone).
- `backend/requirements.txt` (fastapi, uvicorn[standard], httpx, pydantic, pydantic-settings, rapidfuzz,
  psycopg[binary] or asyncpg, pgvector, beautifulsoup4, langdetect, python-telegram-bot, python-docx, pypdf,
  pytest, pytest-asyncio, respx).
- `core/config.py` (pydantic-settings from env, §14), `core/logging.py` (JSON logs), `.env.example` (§14),
  `.gitignore` additions (`.env`, `data/` if needed), `Makefile` (`test`, `run`, `smoke`, `ingest`, `bench`).
- `main.py` with lifespan, CORS from `ALLOWED_ORIGINS`, `GET /health` (DB check added in B04).
AC:
- `uvicorn app.main:app` starts; `GET /health` returns 200.
- `pytest -q` runs (zero tests is fine).
- No secret values anywhere in the repo.

### [x] B02 · Source smoke test + fixtures (P0)
Depends on: B01
Build: `scripts/smoke_sources.py` (§4.3): one real request each to QuranEnc (list + one sura), HadeethEnc
(categories, list, one, search if it exists), Dorar (`dorar_api.json?skey=` with a common Arabic phrase taken
from a HadeethEnc response, not typed by you), Mushaf source; print latency + field names; save raw responses
to `backend/tests/fixtures/<source>/`. Write `docs/SOURCES.md` (source, URL, what we use, license/terms).
AC:
- Script runs end to end and prints a pass/fail line per source.
- One fixture file per source committed.
- Any mismatch with SPEC §4 is written in `docs/DECISIONS.md` with the code change it implies.
- QuranEnc translation keys for en and ur chosen from the real list and written to `.env.example` comments
  and `docs/SOURCES.md` (§4.2).
HUMAN: none, unless a source needs a key or blocks the request.

### [x] B03 · LLM + embeddings clients (P0)
Depends on: B01
Build: `llm/client.py` with `async complete_json(prompt_name, variables, schema: type[BaseModel], model) -> (BaseModel, usage)`:
loads `prompts/<name>.txt`, fills variables, calls the provider chosen by `LLM_PROVIDER`
(`anthropic` and `openai_compatible`), temperature 0, JSON output, pydantic validation, one retry on invalid
JSON, 8 s timeout (configurable), returns token usage. `llm/embeddings.py` with `async embed(texts) -> list[list[float]]`
(batching 100, `EMBEDDING_DIM` enforced, asserts length).
AC:
- Unit tests with mocked HTTP (respx) for: valid JSON, invalid-then-valid JSON (retry), timeout.
- Switching provider is env-only.
HUMAN: choose providers and set `LLM_*` and `EMBEDDING_*` keys in `.env`.

### [ ] B04 · Database + migration (P0)
Depends on: B01
Build: `db/migrations/001_init.sql` exactly as §5 (includes `check_results`, `match_verse`), `db/session.py`
(async pool), `db/queries.py`. `/health` now runs `select 1` (§14, AMENDMENT 8). Background task deleting
expired `check_results` at startup and hourly.
AC:
- Migration applies cleanly on a fresh Supabase project.
- `/health` returns 503 when DB is unreachable, 200 otherwise.
HUMAN: create the Supabase project, enable `vector`, give `DATABASE_URL`.
STATUS 2026-10-05: code done; migration + /health + cleanup verified on a local Postgres 16 + pgvector 0.8.1
(`pytest --live`). Not ticked until the migration is applied to the Supabase project (needs `DATABASE_URL`).

---

## Phase B — Data

### [x] B05 · Mushaf ingest + in-memory index (P0)
Depends on: B02, B04
Build: `scripts/ingest_quran.py` (§6) from the King Fahd Complex data, or the QuranEnc `arabic_text` fallback
if the Complex data is not downloadable without registration (record which in DECISIONS.md).
Also writes `data/quran.json` (surah, ayah, uthmani, clean, surah names ar/en). `pipeline/normalize.py` (§7.1).
App lifespan loads the Mushaf + 2- and 3-verse windows into memory.
AC:
- Exactly 6,236 verses; spot-check printed for 1:1, 2:255, 114:6 (from the data, not typed).
- `tests/test_normalize.py`: tashkeel, tatweel, hamza forms, ta marbuta, alif maqsura, punctuation/digits removal.
- Startup loads the index in < 3 s and memory stays < 300 MB.

### [x] B06 · QuranEnc translations ingest (P0)
Depends on: B05
Build: `sources/quranenc.py` client + `scripts/ingest_quranenc.py` for the pinned en and ur keys.
AC: 6,236 rows per language in `quran_translations`; idempotent re-run inserts nothing new.

### [x] B07 · HadeethEnc ingest (P0)
Depends on: B04, B02
Build: `sources/hadeethenc.py` + `scripts/ingest_hadeethenc.py` (§6): categories → IDs (dedupe) → each hadith in
ar, en, ur. Priority categories first; `--all` for the rest. Concurrency 4, backoff.
AC: progress logged; re-runnable; at least the priority categories ingested with ar+en (ur where available);
counts written to `docs/SOURCES.md`.

### [ ] B08 · Embed corpus (P0)
Depends on: B03, B06, B07
Build: `scripts/embed_corpus.py` (§6), skip rows already embedded.
AC: all `quran_translations` and `hadith_translations` rows embedded; `match_hadith` and `match_verse`
return sensible top results for 3 queries built from ingested translations.
STATUS 2026-10-05: `scripts/embed_corpus.py` done (batches of 100, skips embedded rows, resumable); SQL path
(embed -> update -> `match_verse`) verified on local Postgres with a stub embedder (`tests/test_embed_corpus_live.py`).
Not ticked until run with real `EMBEDDING_*` keys and the 3-query sanity check.

### [ ] B09 · Dorar client (P0)
Depends on: B02, B04
Build: `sources/dorar.py` (§4.1): call official API, parse `ahadith.result` HTML with BeautifulSoup into
`DorarResult(id, text, text_clean, narrator, mohaddith, book, page, grade_text, url)`, stable IDs, cache in
`dorar_cache`, 8 s timeout, one retry, `SourceUnavailable` exception on failure.
AC:
- `tests/test_dorar_parser.py` parses the saved fixture: count, and every field non-empty where the HTML has it.
- Second identical query is served from cache (no HTTP, verified with respx).
- On timeout raises `SourceUnavailable` (never returns an empty list silently).

---

## Phase C — Pipeline

### [ ] B10 · Extraction + rule detector (P0)
Depends on: B03
Build: `prompts/extract.txt` and schema exactly as §7.2 (AMENDMENT 3, 7), `pipeline/extract.py`,
`pipeline/rules_detect.py` (trigger list in §7.2), merge logic, span-substring validation, cap 10 claims.
AC:
- Unit tests with mocked LLM: claim kept; hallucinated span dropped; rule-only claim added; `intent` values
  map correctly; text containing "ignore previous instructions" does not change behaviour (mock returns schema-valid output; assert prompt wraps text in `<message>`).
- One live test (marked `@pytest.mark.live`, skipped by default) on 3 real messages.

### [ ] B11 · Verse matcher with alignment (P0) — AMENDMENT 4
Depends on: B05
Build: `pipeline/quran_match.py` exactly as §7.3 (raw score to classify, adjusted to rank, containment guard,
`partial_ratio_alignment` + word-boundary expansion, edge rule, multi-location, attribution check),
`pipeline/surah_parse.py` (Arabic/English surah names, `2:255`, `البقرة 255`, Arabic-Indic digits),
thresholds in `core/thresholds.py`.
AC: `tests/test_quran_match.py` implements **all six cases in §7.3** built from `data/quran.json` by reference,
and they pass. Plus surah parser tests (5 formats).

### [ ] B12 · Non-Arabic verse path (P0)
Depends on: B08, B11
Build: vector search via `match_verse` + literal Arabic queries through B11, merge, top 6 to verifier.
AC: given an approved English and Urdu translation of 3 verses (taken from `quran_translations`), the correct
verse is in the top 6 candidates for all 6.

### [ ] B13 · Hadith retriever (P0)
Depends on: B08, B09
Build: `pipeline/hadith_retrieve.py` §7.4: paths A/B/C in parallel, early stop, grouping of Dorar results by
matn (token_set_ratio ≥ 92), top 6 to verifier, `source_status` propagation.
AC:
- Fixture test: Dorar fixture with several chains of one matn → one group carrying all gradings.
- If Dorar raises `SourceUnavailable`, paths B/C still run and `source_status = "source_unavailable"` is set.

### [ ] B14 · Verifier (P0) — AMENDMENT 2, 3
Depends on: B03
Build: `prompts/verify.txt` and `pipeline/verify.py` as §7.5: multi `match_ids`, ID validation in code,
hallucination logging, per-type confidence thresholds (hadith `same_meaning` ≥ 0.85).
AC: unit tests with mocked LLM: unknown ID dropped and logged; all-unknown → no match; hadith
`same_meaning` at 0.80 → rejected; verse `same_meaning` at 0.80 → accepted.

### [ ] B15 · Grade classifier + decision engine (P0) — AMENDMENT 1, 5 — `NEEDS SH SIGN-OFF`
Depends on: B11, B13, B14
Build: `core/grade_rules.py` (keyword lists), `pipeline/grades.py` (§8.1), `pipeline/decide.py` (§8.2–8.4:
aggregation table, altered → misquoted, attribution, wrong_type, out_of_scope_attribution, source_unavailable,
message-level status).
AC:
- `tests/test_grades.py`: all 8 cases in §8.1.
- `tests/test_decide.py`: one test per row of the §8.2 table (7) + altered/verified → misquoted +
  attribution mismatch + source_unavailable never yields a positive verdict + each message status in §8.4.
HUMAN: send `core/grade_rules.py` and the §8.2 table to the sharia reviewer; apply their edits.

### [ ] B16 · Orchestrator + `/api/v1/check` (P0)
Depends on: B10–B15
Build: `pipeline/orchestrator.py` §7.7, `models/result.py` matching `docs/API_CONTRACT.md` exactly,
`api/check.py`, `GET /api/v1/check/{id}`, `api/sources.py`, rate limit (`core/ratelimit.py`, 20/min/IP),
input limit, storage in `check_results` (24 h), metrics in `check_metrics`, LRU cache, disclaimers by language
from `core/messages.py`.
AC:
- Contract test: response validates against the pydantic model AND against the example JSON in API_CONTRACT.
- End-to-end test with all external calls mocked from fixtures: an Arabic message with one verse and one hadith
  returns two correct cards.
- Live smoke (`@live`): 3 example messages (ar, en, ur) under 12 s each.
- Logs contain no message text (assert in a test by capturing logs).

### [ ] B17 · Deploy backend (P0)
Depends on: B16
Build: `render.yaml` (or documented manual settings), start command per §14, env vars list, README deploy section.
AC: public URL `/health` 200; `/api/v1/check` works from an external machine; frontend teammate given the URL.
HUMAN: create the Render service, paste env vars, set up the pinger (UptimeRobot or cron-job.org) on `/health` every 10 min.

---

## Phase D — Evaluation (start B18 in parallel with Phase C)

### [ ] B18 · Mizan-Bench builder (P0) — `NEEDS SH SIGN-OFF`
Depends on: B02 (sources), B05–B09 for data
Build: `bench/build_items.py` that seeds items **from source data only** (§11.1–11.2): authentic hadiths
(HadeethEnc + Dorar), authentic hadiths that also have weak chains (Dorar results where a Sahihayn grading and
a weak grading coexist), widespread unestablished hadiths (Dorar), verses + rule-based alterations + partial
quotes + multi-location phrases (from `data/quran.json`), translated variants from approved translations,
the reference pack page-6 questions, personal-ruling prompts. Fabricated-translated-with-lookalike items:
pair a not-established hadith with the nearest authentic HadeethEnc hadith by embedding similarity and keep
the pair only if similarity ≥ 0.6. Writes `bench/items.jsonl` with `reviewed_by: null` and a 30/70 dev/test split
stratified by category. Also `bench/review_sheet.csv` for the sharia reviewer (id, category, text, expected, approve Y/N, note).
AC: ≥ 150 items with the §11.2 proportions; every item has `provenance`; no item text typed by hand.
HUMAN: sharia reviewer fills `review_sheet.csv`; run `bench/build_items.py --apply-review` to set `reviewed_by`.

### [ ] B19 · Bench runner, baselines, metrics (P0) — AMENDMENT 9
Depends on: B16, B18
Build: `bench/run.py` (systems `mizan`, `llm_baseline`, `dorar_direct`; `--runs`; saves raw outputs to
`bench/results/<system>-<split>-<run>.jsonl`), `bench/metrics.py` (all metrics in §11.3, hallucination per §11.4,
tables + PNG charts, `report.md`), `bench/prices.json`.
AC:
- `llm_baseline` uses the forced structured output in §11.4.
- Report shows per-category and per-language accuracy, not-established recall, false-verified rate, hallucination
  rate, consistency, latency p50/p95, mean cost per check, and reviewed vs unreviewed counts.
- Manual-agreement field for the 30-sample baseline review is present in the report template.

### [ ] B20 · Threshold tuning on dev, freeze, run test (P0)
Depends on: B19
Build: `bench/tune.py` sweeping the thresholds in `core/thresholds.py` on `dev` only; pick values maximizing
accuracy subject to false-verified = 0; write them to `thresholds.py`; tag the commit `thresholds-frozen`;
run test split (mizan ×3, llm_baseline ×3, dorar_direct ×1); write `docs/EVALUATION.md`.
AC: EVALUATION.md has method, dev/test separation statement, tables, charts, limitations per language.

---

## Phase E — P1 features

### [ ] B21 · Ready reply + `/api/v1/reply` (P1) — AMENDMENT 10
Depends on: B16
Build: `prompts/reply.txt` §7.6 with glossary, `pipeline/reply.py`, URL validation + one regeneration,
caching the reply in `check_results.reply`.
AC: mocked test where the model adds a foreign URL → regenerated → still bad → `reply: null` with
`reply_error`; live test (`@live`) on one en and one ur result.

### [ ] B22 · Authentic alternative (P1)
Depends on: B13, B15
Build: `pipeline/alternative.py` §8.5.
AC: alternative only for `not_established`; below 0.75 similarity → none; labelled as a different hadith.

### [ ] B23 · Feedback endpoint (P1)
Depends on: B16
AC: `POST /api/v1/feedback` validates `issue` enum, stores without message text, 404 on unknown `check_id`.

### [ ] B24 · Telegram bot (P1) — AMENDMENT 12
Depends on: B16, B21
Build: §10 in full (text or caption, HTML escaping, edit-in-place, buttons, 4,096 split on card boundaries,
`/start` in ar/en/ur, 10 msgs/min per user with hashed user ID, webhook secret).
AC: unit tests for escaping and splitting; manual test from a phone forwarding a captioned image.
HUMAN: create the bot with BotFather, set `TELEGRAM_BOT_TOKEN`, run `setWebhook` (provide the exact command).

---

## Phase F — Ship

### [ ] B25 · Warm cache, cost report, docs (P0)
Depends on: B17, B20
Build: `scripts/warm_cache.py`, `scripts/cost_report.py`; finish `README.md` (idea, architecture diagram as
Mermaid, setup from scratch, run, demo link, bench summary), `docs/ARCHITECTURE.md`, `docs/LICENSES.md`,
`docs/CONTENT_POLICY.md` (content levels A–D handling, abstention, referral, disclaimer, privacy text from §13).
AC: a teammate can follow README on a clean machine; `gitleaks detect` (or equivalent) is clean.

### [ ] B26 · Pre-submission checklist (P0)
Depends on: everything P0
AC: every item of SPEC §18 checked and ticked in this file; print the final live URL, repo URL, and the 3
demo inputs with their verdicts for the video.

### [ ] B27 · File report (P2)
Depends on: B16
Build: `POST /api/v1/check/file` (TXT/DOCX/PDF ≤ 5 MB, split into paragraphs, batch through orchestrator, aggregate).
