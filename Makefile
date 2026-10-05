PY ?= python

.PHONY: install test test-live run smoke ingest embed bench

install:
	cd backend && $(PY) -m pip install -r requirements.txt

test:
	cd backend && $(PY) -m pytest -q

test-live:
	cd backend && $(PY) -m pytest -q --live

run:
	cd backend && $(PY) -m uvicorn app.main:app --reload --port 8000

smoke:
	$(PY) scripts/smoke_sources.py

ingest:
	$(PY) scripts/ingest_quran.py
	$(PY) scripts/ingest_quranenc.py
	$(PY) scripts/ingest_hadeethenc.py

embed:
	$(PY) scripts/embed_corpus.py

bench:
	$(PY) bench/run.py --system mizan --split test --runs 3
	$(PY) bench/metrics.py --out bench/results/report.md
