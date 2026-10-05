-- [DECISION D-14] Surah names (from the Mushaf source) so the server can load the Mushaf from the DB
-- when data/quran.json is absent; locations in the API contract carry surah_name_ar / surah_name_en.
alter table quran_verses add column if not exists surah_name_ar text;
alter table quran_verses add column if not exists surah_name_en text;
