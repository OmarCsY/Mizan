# Sources

Every religious text, grading, book name and link that Mizan shows comes from one of these sources.
Checked live by `scripts/smoke_sources.py` (last run: 2026-10-05, all required sources PASS).
Raw sample responses are in `backend/tests/fixtures/<source>/`.

| Source | URL | What we use | Access | License / terms |
|---|---|---|---|---|
| Mushaf (King Fahd Complex, Hafs v3.0) | `https://download.qurancomplex.gov.sa/resources_dev/kfgqpc_hafs_v30.zip` (listed on `qurancomplex.gov.sa/quran-dev`) | `aya_text_unicode` (Uthmani, display + matching form a), `aya_text_emlaey` (imla'i, matching form b, DECISIONS D-3), `sura_name_ar`, `sura_name_en` | Direct download, no registration. 6,236 rows, one per verse, both forms in the same row, so (surah, ayah) alignment is 1:1 by construction. | No explicit license on the page; footer says "جميع الحقوق محفوظة". We therefore **do not commit** the zip or `data/quran.json` (gitignored); `scripts/ingest_quran.py` downloads it. Only a 6-verse sample is committed as a test fixture. See DECISIONS D-10. |
| QuranEnc | `https://quranenc.com/api/v1` | Approved translations: `translations/list/{lang}`, `translation/sura/{key}/{sura}`, `translation/aya/{key}/{sura}/{aya}`. Fields: `id, sura, aya, arabic_text, translation, footnotes`. | Public REST, no key. | Terms to be confirmed and recorded in `docs/LICENSES.md` (B25). |
| HadeethEnc | `https://hadeethenc.com/api/v1` | `languages`, `categories/list/?language=ar` (493 categories, 7 roots), `hadeeths/list/?language&category_id&page&per_page` (`data[]`, `meta.last_page/total_items`), `hadeeths/one/?id&language` (`hadeeth, attribution, grade, explanation, reference, ...`; non-ar responses also carry `hadeeth_ar`, `grade_ar`, `attribution_ar`). | Public REST, no key. `hadeeths/search/` exists but returns HTTP 400 for every parameter tried, so path C uses local rapidfuzz (DECISIONS D-8). | Terms to be confirmed and recorded in `docs/LICENSES.md` (B25). |
| Dorar hadith search (official API) | `https://dorar.net/dorar_api.json?skey=<query>` (documented at `dorar.net/article/389`) | `ahadith.result`: an HTML string with 15 results. Per result: `div.hadith` (text) + `div.hadith-info` with `الراوي`, `المحدث`, `المصدر`, `الصفحة أو الرقم`, `خلاصة حكم المحدث`. One "المزيد" link per response: `https://dorar.net/hadith/search?q=<query>`. Optional params confirmed: `page=N`; `s[]=<book id>` (`6216` = صحيح البخاري, `3088` = صحيح مسلم). | Public, no key. Live call + `dorar_cache`. | Terms to be confirmed and recorded in `docs/LICENSES.md` (B25). Gradings are shown verbatim with scholar and book, as the source gives them. |
| Association MCP server (optional) | `https://mcp.islamiccontent.org` | Not used on the critical path. Reachable (HTTP 200). | — | — |

## Pinned QuranEnc translation keys (SPEC §4.2)

Chosen from the live lists on 2026-10-05. Indexing and display must use the same keys.

| Lang | Key | Title (from the API) | Version |
|---|---|---|---|
| en | `english_rwwad` | English Translation - Rowwad Translation Center | 1.0.19 |
| ur | `urdu_junagarhi` | Urdu Translation - Muhammad Junagarhi | 1.1.3 |

Other keys returned for en: `english_saheeh`, `english_hilali_khan`. Only one key exists for ur.

## Ingest counts

Filled in by B05–B07.
