"""User-facing strings in Arabic / English / Urdu (CLAUDE.md conventions).

The privacy text is served as `privacy` in `GET /api/v1/sources` (the About page, API_CONTRACT.md).
It includes the free-tier provider notice (DECISIONS D-15).
"""

from __future__ import annotations

Lang = str

DISCLAIMER: dict[Lang, str] = {
    "ar": "أداة آلية للتحقق من المصادر، وليست فتوى.",
    "en": "An automated source-verification tool, not a fatwa.",
    "ur": "یہ ذرائع کی جانچ کا ایک خودکار آلہ ہے، فتویٰ نہیں۔",
}

# SPEC §13 privacy text + free-tier provider notice (D-15).
PRIVACY: dict[Lang, str] = {
    "ar": (
        "تُحفظ نتيجة التحقق 24 ساعة لعرض التفاصيل والرد الجاهز ثم تُحذف. "
        "لا تُحفظ هوية المرسل ولا يُستنتج منها أي شيء عن معتقده. "
        "يعمل ميزان على الخطة المجانية لمزوّد النموذج اللغوي، وقد يستخدم المزوّد النصوص المُرسلة "
        "لتحسين خدماته؛ فلا تُرسل بيانات شخصية أو حساسة."
    ),
    "en": (
        "A check result is stored for 24 hours to show its details and the ready reply, then deleted. "
        "The sender's identity is not stored and nothing is inferred about their beliefs. "
        "Mizan runs on the free tier of its language-model provider, which may use submitted text to "
        "improve its services, so do not submit personal or sensitive information."
    ),
    "ur": (
        "جانچ کا نتیجہ تفصیلات اور تیار جواب دکھانے کے لیے 24 گھنٹے محفوظ رہتا ہے، پھر حذف کر دیا جاتا ہے۔ "
        "بھیجنے والے کی شناخت محفوظ نہیں کی جاتی اور اس سے اس کے عقیدے کے بارے میں کوئی نتیجہ اخذ نہیں کیا جاتا۔ "
        "میزان اپنے لسانی ماڈل فراہم کنندہ کے مفت پلان پر چلتا ہے، اور فراہم کنندہ بھیجے گئے متن کو اپنی خدمات "
        "بہتر بنانے کے لیے استعمال کر سکتا ہے؛ اس لیے ذاتی یا حساس معلومات نہ بھیجیں۔"
    ),
}


def localized(table: dict[Lang, str], lang: str | None) -> str:
    return table.get(lang or "ar", table["ar"])
