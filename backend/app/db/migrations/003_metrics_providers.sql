-- [DECISION D-15] Which LLM provider served each check (calls per provider, e.g. {"gemini": 2, "groq": 1}),
-- so free-tier fallbacks are visible in cost/latency reporting.
alter table check_metrics add column if not exists llm_providers jsonb;
