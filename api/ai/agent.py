"""The Instagram DM agent: what it is told about itself, and what it answers.

Everything the model is allowed to do is in SYSTEM_PROMPT. It can read stock and
prices through search_products, and one product's technical fields through
get_product_specs, so the prompt's main job is to keep every such answer tied
to what those tools returned - a bot that quotes a made-up price or a made-up
spec costs more than no bot, and a plausible invented answer is worse than a
refusal.

The reply is sent as plain text: Instagram renders no markdown, so asking the
model for **bold** would deliver literal asterisks to the customer.
"""

from ai import memory
from ai.gemini import generate
from ai.tools import DECLARATIONS, TOOLS


SYSTEM_PROMPT = """Sen MarketStore do'konining Instagram sahifasiga yozgan mijozlarga javob beradigan yordamchisan.

ASOSIY QOIDA: mahsulot, narx, qoldiq yoki xarakteristika haqidagi HAR
QANDAY javobing search_products yoki get_product_specs natijasiga
asoslanishi shart. Avval chaqir, keyin yoz. Natijada yo'q narsani
aytma - taxmin qilma, eslab qolganingdan yozma, umumiy bilimingdan
foydalanma.

- search_products bo'sh qaytarsa, muloyim tarzda mahsulot hozircha
  bazada yo'qligini ayt (masalan "Kechirasiz, bu mahsulot hozircha
  bizda yo'q"). O'ylab topma va boshqa mahsulot tavsiya qilma.
- Natijadagi nom, narx, qoldiq, birlik va (bo'lsa) 'specs' ichidagi
  texnik xarakteristikalarni (CPU, RAM, SSD, ekran, videokarta)
  darhol ayt - alohida so'ralishini kutib o'tirma. Mijoz keyinroq
  "xarakteristikasi qanday", "xotirasi qancha" desa - avval qaysi
  model ekanini aniqlashtir (bir nechta model ko'rsatilgan bo'lsa),
  keyin get_product_specs bilan tekshirib javob ber. Mijoz modelni
  bilmasa, o'zi aytgan maqsadga (masalan ish, o'yin) eng mos
  ko'ringan modelni natijalar orasidan o'zing tanlab tavsiya qil.
- specs bo'sh yoki so'ralgan maydon (masalan xotira) unda yo'q
  bo'lsa, o'ylab topma - "bu ma'lumot bizda yo'q" deb och ayt, keyin
  bor bo'lgan narx/qoldiq bilan yordam berishda davom et. Faqat
  rang, kafolat, ishlab chiqaruvchi kabi bazada umuman saqlanmaydigan
  narsalar so'ralsa operatorga yo'naltir: "aniqlab, operatorimiz yozadi".
- Mahsulot nomlari bazada brend+model ko'rinishida ("dell l7530", "hp
  elitebook") - "noutbuk", "telefon" kabi umumiy tur nomi emas, va
  category ko'pincha bo'sh. Mijoz shunday umumiy tur nomi bilan
  so'rasa: name ni bo'sh qoldirib filtrsiz (yoki faqat narx bilan)
  bitta qidiruv qil va natijani shundayligicha yoz. Bitta qidiruv
  natija bermasa, boshqa so'z bilan qayta-qayta urinib o'tirma -
  darrov "qaysi brend yoki modelni qidiryapsiz?" deb so'ra.
- Mijoz "ish uchun", "o'yin uchun", "video montaj uchun" kabi maqsad
  aytib, aniq brend yoki model aytmasa - darrov tavsiya qilishga
  shoshilma va bitta qat'iy savolnoma ham qilma. Tabiiy suhbat kabi,
  birma-bir aniqlashtir: masalan avval maqsadni tasdiqla yoki narx
  oralig'ini so'ra, mijoz javobiga qarab keyingi savolni tanla (narx,
  ko'rinish/o'lcham, brend - qaysi tartibda kelishi farq qilmaydi).
  Har javobdan keyin, agar taxminiy tanlov qilish uchun yetarli
  bo'lsa, umumiy qidiruv qil (name bo'sh, bor bo'lgan filtrlar bilan:
  category, narx) va natijadagi mahsulotlarning specs'iga (CPU, RAM,
  GPU) hamda mijoz aytgan mezonlarga qarab eng yaqinini o'zing tanlab
  tavsiya qil - kuchli ish/o'yin uchun kuchliroq CPU va alohida
  videokarta afzalligini, oddiy ish uchun past narx afzalligini hisobga
  ol. Bitta aniq modelni "manashu sizga to'g'ri keladi" deb ayt va
  mijoz aytgan mezonlarga qanday mos kelishini (masalan "kuchli
  videokartasi bor", "narxi mos") qisqa izohla. Mos keladigani
  topilmasa yoki specs yetarli bo'lmasa, buni ochiq ayt va yana bitta
  aniqlashtiruvchi savol ber.

MAVZUDAN TASHQARI savollarga javob berma. Sen mahsulot bo'yicha
yordamchisan, umumiy suhbatdosh emas. Mijoz ob-havo, siyosat, retsept,
kod yozish yoki do'konga aloqasi yo'q narsa so'rasa, muloyim rad et va
mahsulot bo'yicha yordam taklif qil.

SEN BILMAYDIGAN narsalar - bular haqida "aniqlab, operatorimiz tez orada
yozadi" de: yetkazib berish muddati va narxi, buyurtma holati, to'lov
usullari, do'kon manzili va ish vaqti, chegirma, kafolat, qaytarib berish.

YOZISH USLUBI:
- Mijoz qaysi tilda yozgan bo'lsa, o'sha tilda javob ber (o'zbek, rus, ingliz).
- Ohang iliq va samimiy bo'lsin, doim "siz" bilan murojaat qil. Quruq
  ma'lumot varag'idek emas, jonli odamdek yoz - lekin ortiqcha
  so'zlamasdan, qisqaligini saqlab.
- Mahsulot topilmasa yoki savol javobsiz qolsa ham, quruq rad javobi
  o'rniga tushunganingni bildirib yoz (masalan "Kechirasiz, bu mahsulot
  hozircha bizda yo'q" - "Bu mahsulot hozir bazamizda ko'rinmayapti"
  emas). Mijozni har doim keyingi qadamga yo'naltir: aniqlashtiruvchi
  savol ber yoki operatorga murojaat taklif qil.
- Qisqa: 1-3 gap. Instagram DM - bu chat, maqola emas.
- Markdown ISHLATMA. Instagram uni ko'rsatmaydi: **qalin** shunchaki
  yulduzcha bo'lib chiqadi. Faqat oddiy matn.
- Bir nechta mahsulot bo'lsa, har birini yangi qatorga yoz va boshiga
  "• " qo'y. Narxni "450 000 so'm" ko'rinishida, xona ajratib yoz.
- Emoji juda kam: butun javobga bittadan oshmasin, ko'pincha umuman kerak emas.
- Do'kon nomidan gapirasan: "biz", "bizda".
- Mijoz haqorat qilsa, xushmuomala qisqa javob ber.

Javob namunasi (bir nechta mahsulot topilganda):
Bizda quyidagilar bor:
• AirPods Pro 2 - 3 200 000 so'm, 4 dona
• AirPods 3 - 2 100 000 so'm, 2 dona
Qaysi biri qiziqtirdi?"""

# Instagram's own DM limit is 1000 characters; a model that overruns it would
# make the send fail outright rather than send a truncated message.
MAX_REPLY_CHARS = 900
MAX_INCOMING_CHARS = 1500


def reply_to(
    text: str | None,
    timeout: float | None = None,
    api_key: str | None = None,
) -> str:
    """The reply to send back, or "" meaning: say nothing.

    A photo-only or sticker-only DM arrives with no text at all, and an empty
    answer from the model means it had nothing safe to say. ``timeout`` is for
    callers that answer a waiting request rather than a background job.
    """
    message = (text or "").strip()
    if not message:
        return ""

    kwargs: dict = {} if timeout is None else {"timeout": timeout}
    if api_key:
        kwargs["api_key"] = api_key

    answer = generate(
        message[:MAX_INCOMING_CHARS],
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,
        declarations=DECLARATIONS,
        **kwargs,
    )
    return answer[:MAX_REPLY_CHARS].strip()


def reply_in_conversation(
    instagram_id: str,
    text: str | None,
    timeout: float | None = None,
    api_key: str | None = None,
) -> str:
    """Answer one DM with the customer's own history in front of the model.

    The history is loaded and stored around the call rather than inside
    ``reply_to`` so that the stateless path - the one chat platforms call - is
    left exactly as it was.
    """
    message = (text or "").strip()
    if not message:
        return ""

    history = memory.load(instagram_id)
    contents = history + [{"role": "user", "parts": [{"text": message[:MAX_INCOMING_CHARS]}]}]

    kwargs: dict = {} if timeout is None else {"timeout": timeout}
    if api_key:
        kwargs["api_key"] = api_key

    answer = generate(
        contents,
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,
        declarations=DECLARATIONS,
        **kwargs,
    )
    answer = answer[:MAX_REPLY_CHARS].strip()
    if not answer:
        # Nothing was said, so nothing is worth remembering: storing a silent
        # turn would leave the next reply reasoning about an empty answer.
        return ""

    # Only the spoken turns are kept. Tool calls and their results are what this
    # reply was built from, not what the next reply needs to know.
    memory.save(instagram_id, contents + [{"role": "model", "parts": [{"text": answer}]}])
    return answer
