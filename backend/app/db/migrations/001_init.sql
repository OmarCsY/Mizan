-- Mizan schema (SPEC §5). Applied by scripts/migrate.py.
create extension if not exists vector;

-- Quran: one row per verse
create table quran_verses (
  id                int primary key,          -- global 1..6236
  surah             smallint not null,
  ayah              smallint not null,
  text_uthmani      text not null,            -- display
  text_clean        text not null,            -- normalized Uthmani, for matching
  text_imlaei_clean text not null,            -- [DECISION D-3] normalized imla'i, for matching
  unique (surah, ayah)
);

-- Approved translations (QuranEnc)
create table quran_translations (
  verse_id   int references quran_verses(id),
  lang       text not null,                   -- 'en' | 'ur' | 'id'
  tr_key     text not null,                   -- QuranEnc translation key
  text       text not null,
  embedding  vector(1024),
  primary key (verse_id, tr_key)
);

-- Authentic hadiths with translations (HadeethEnc)
create table hadiths (
  id             int primary key,             -- HadeethEnc id
  text_ar        text not null,
  text_ar_clean  text not null,
  attribution    text,                        -- e.g. "رواه مسلم" as given by source
  grade          text,                        -- as given by source
  url            text not null
);

create table hadith_translations (
  hadith_id    int references hadiths(id),
  lang         text not null,                 -- 'ar' | 'en' | 'ur'
  text         text not null,
  explanation  text,
  embedding    vector(1024),
  primary key (hadith_id, lang)
);

-- Cache of Dorar searches
create table dorar_cache (
  query_hash  text primary key,               -- sha1(normalized query)
  query       text not null,
  response    jsonb not null,                 -- parsed results, not raw HTML
  fetched_at  timestamptz default now()
);

-- [AMENDMENT 6] Short-lived results for /reply, result page and bot "details"
create table check_results (
  check_id    text primary key,               -- uuid4 hex, unguessable
  result      jsonb not null,
  reply       jsonb,                          -- cached ready reply per lang
  expires_at  timestamptz not null            -- now() + 24h
);
create index on check_results (expires_at);

-- Feedback (no message text)
create table feedback (
  id           bigserial primary key,
  check_id     text not null,
  claim_index  int not null,
  issue        text not null,                 -- 'wrong_verdict' | 'wrong_source' | 'other'
  note         text,
  created_at   timestamptz default now()
);

-- Per-check telemetry (cost and latency reporting)
create table check_metrics (
  check_id        text primary key,
  channel         text, lang text, n_claims int, status text,
  latency_ms      int, llm_tokens_in int, llm_tokens_out int,
  embed_tokens    int,
  created_at      timestamptz default now()
);

create index on quran_translations  using hnsw (embedding vector_cosine_ops);
create index on hadith_translations using hnsw (embedding vector_cosine_ops);

create or replace function match_hadith(q vector(1024), q_lang text, k int)
returns table (hadith_id int, lang text, text text, score float)
language sql stable as $$
  select hadith_id, lang, text, 1 - (embedding <=> q) as score
  from hadith_translations
  where lang = q_lang or lang = 'ar'
  order by embedding <=> q
  limit k;
$$;

create or replace function match_verse(q vector(1024), q_lang text, k int)
returns table (verse_id int, lang text, text text, score float)
language sql stable as $$
  select verse_id, lang, text, 1 - (embedding <=> q) as score
  from quran_translations
  where lang = q_lang
  order by embedding <=> q
  limit k;
$$;
