# Content policy

How Mizan handles content, abstention, referral and privacy. The full policy (content levels A–D,
abstention, referral, disclaimer) is completed in task B25; this file starts with the privacy section.

## Privacy

User-facing text (served as `privacy` by `GET /api/v1/sources` for the About page, in Arabic, English and
Urdu; source: `backend/app/core/messages.py`):

> تُحفظ نتيجة التحقق 24 ساعة لعرض التفاصيل والرد الجاهز ثم تُحذف. لا تُحفظ هوية المرسل ولا يُستنتج منها أي
> شيء عن معتقده. يعمل ميزان على الخطة المجانية لمزوّد النموذج اللغوي، وقد يستخدم المزوّد النصوص المُرسلة
> لتحسين خدماته؛ فلا تُرسل بيانات شخصية أو حساسة.

> A check result is stored for 24 hours to show its details and the ready reply, then deleted. The sender's
> identity is not stored and nothing is inferred about their beliefs. Mizan runs on the free tier of its
> language-model provider, which may use submitted text to improve its services, so do not submit personal
> or sensitive information.

What this means in practice:

- **Model provider (free tier, DECISIONS D-15).** Message text is sent to the language-model provider
  (Google Gemini; Groq as fallback when Gemini's quota is exhausted) for extraction, comparison and the
  ready reply, and quoted spans are sent to the embedding provider (Gemini). On free tiers the provider may
  use submitted content to improve its services. This is why the notice above is shown on the About page.
- **Storage.** Check results (which contain the quoted spans) are kept 24 hours in `check_results`, then
  deleted by an hourly job (SPEC §5, §13). Dorar search queries derived from quotes are cached in
  `dorar_cache` for the duration of the challenge.
- **Logs and telemetry.** Logs never contain message text. `check_metrics` stores counts, latency, token
  usage and which provider served each call, never text.
- **Telegram.** User IDs are kept only as a salted hash for rate limiting (SPEC §13).
- **No profiling.** Nothing is inferred about a user's beliefs.
