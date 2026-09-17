"""The Instagram DM agent: what it is told about itself, and what it answers.

Everything the model is allowed to do is in SYSTEM_PROMPT. It has no access to
the shop's stock or prices yet, so the prompt's main job is to keep it from
inventing them - a bot that quotes a made-up price costs more than no bot.
"""

from ai.gemini import generate


SYSTEM_PROMPT = """Sen MarketStore do'konining Instagram sahifasiga yozgan mijozlarga javob beradigan yordamchisan.

Qoidalar:
- Mijoz qaysi tilda yozgan bo'lsa, o'sha tilda javob ber (o'zbek, rus yoki ingliz).
- Qisqa yoz: 1-3 gap. Instagram DM - bu chat, maqola emas.
- Narx, mavjudlik, yetkazib berish muddati va buyurtma holatini SEN BILMAYSAN.
  Bunday savolga taxmin qilib javob berma - "aniqlab, operatorimiz tez orada
  yozadi" deb ayt.
- Havola, telefon raqam yoki manzil o'ylab topma.
- Chegirma, kafolat yoki qaytarib berish va'dasini berma.
- Do'kon nomidan gapirasan: "biz", "bizda".
- Mijoz haqorat qilsa yoki mavzudan tashqari yozsa, xushmuomala qisqa javob ber."""

# Instagram's own DM limit is 1000 characters; a model that overruns it would
# make the send fail outright rather than send a truncated message.
MAX_REPLY_CHARS = 900
MAX_INCOMING_CHARS = 1500


def reply_to(text: str | None) -> str:
    """The reply to send back, or "" meaning: say nothing.

    A photo-only or sticker-only DM arrives with no text at all, and an empty
    answer from the model means it had nothing safe to say.
    """
    message = (text or "").strip()
    if not message:
        return ""

    answer = generate(message[:MAX_INCOMING_CHARS], system_instruction=SYSTEM_PROMPT)
    return answer[:MAX_REPLY_CHARS].strip()
