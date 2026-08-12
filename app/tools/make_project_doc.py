from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


OUT = Path("Market_POS_individual_loyiha_30_bet.docx")


def p(text="", style=None, break_before=False):
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if break_before:
        props.append("<w:pageBreakBefore/>")
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def bullets(items):
    return "".join(p(f"• {item}") for item in items)


pages = [
    (
        "1-bet. Loyiha nomi va umumiy ma'lumot",
        [
            "Individual loyiha mavzusi: Market POS - do'kon savdo, ombor, mijozlar, ta'minotchilar, xarajatlar va hisobotlarni boshqarish tizimi.",
            "Loyiha Python dasturlash tili, PyQt6 grafik interfeysi va SQLite ma'lumotlar bazasi asosida ishlab chiqiladi.",
            "Tizim kichik va o'rta savdo nuqtalarida mahsulotlarni ro'yxatga olish, sotish, qaytarish, qarzlarni yuritish va kunlik hisobot olish jarayonlarini avtomatlashtirishga xizmat qiladi.",
        ],
        ["Dastur nomi: Market Store POS", "Platforma: Windows desktop", "Asosiy foydalanuvchilar: admin va kassir", "Ma'lumotlar bazasi: SQLite"],
    ),
    (
        "2-bet. Loyiha dolzarbligi",
        [
            "Do'konlarda mahsulot qoldig'i, narx, xarajat va mijoz qarzlarini daftar yoki oddiy jadvalda yuritish ko'p xatolarga olib keladi.",
            "Market POS tizimi savdo jarayonini tezlashtiradi, xatoliklarni kamaytiradi va ma'lumotlarni yagona bazada saqlaydi.",
            "Loyihaning dolzarbligi shundaki, kichik biznes egasi alohida murakkab ERP tizim sotib olmasdan, sodda va tushunarli dasturdan foydalanishi mumkin.",
        ],
        ["Sotuvni tez amalga oshirish", "Ombor qoldig'ini avtomatik yuritish", "Qarz savdoni nazorat qilish", "Mahsulot qaytarishni arxiv orqali bajarish"],
    ),
    (
        "3-bet. Loyiha maqsadi",
        [
            "Loyihaning asosiy maqsadi savdo do'koni uchun sodda, qulay va ishonchli POS tizim yaratishdan iborat.",
            "Tizim foydalanuvchiga mahsulot qo'shish, sotish, qaytarish, mijoz balansini ko'rish, ta'minotchi qarzini yuritish va hisobot olish imkonini beradi.",
            "Dastur lokal kompyuterda ishlaydi va internet talab qilmaydi, bu esa kichik savdo nuqtalari uchun qulay yechim hisoblanadi.",
        ],
        ["Savdo jarayonini avtomatlashtirish", "Mahsulotlar hisobini yuritish", "Arxiv va qaytarish mexanizmini yaratish", "Admin va kassir rollarini ajratish"],
    ),
    (
        "4-bet. Loyiha vazifalari",
        [
            "Loyiha vazifalari tizim modullarini bosqichma-bosqich ishlab chiqish va ularni yagona interfeysga birlashtirishdan iborat.",
            "Har bir modul alohida vazifani bajaradi, lekin ma'lumotlar bazasi orqali bir-biri bilan bog'langan bo'ladi.",
            "Masalan, sotuv amalga oshganda mahsulot qoldig'i kamayadi, qarz savdo bo'lsa mijoz balansi oshadi, qaytarish bo'lsa qoldiq qayta tiklanadi.",
        ],
        ["Login oynasi yaratish", "Mahsulotlar CRUD oynasini yaratish", "Sotuv savatini yaratish", "Arxiv va qaytarish funksiyasini yaratish", "Hisobotlar modulini tayyorlash"],
    ),
    (
        "5-bet. Foydalanuvchilar va rollar",
        [
            "Tizimda ikki asosiy rol mavjud: admin va kassir. Admin barcha modullarni ko'ra oladi, kassir esa asosan sotuv oynasi bilan ishlaydi.",
            "Bunday yondashuv xavfsizlik va tartib uchun muhim. Kassir mahsulotlar bazasini yoki foydalanuvchilarni o'zgartirib yubormaydi.",
            "Admin yangi kassir qo'shishi, parolni yangilashi, mahsulot va hisobotlarni boshqarishi mumkin.",
        ],
        ["Admin: barcha huquqlarga ega", "Kassir: sotuvni amalga oshiradi", "Login tarixi saqlanadi", "Har bir sotuvda kassir nomi arxivga yoziladi"],
    ),
    (
        "6-bet. Texnologiyalar",
        [
            "Loyiha Python dasturlash tilida yoziladi. Python sodda sintaksisi va ko'p kutubxonalari bilan desktop dastur yaratishda qulay.",
            "Interfeys uchun PyQt6 tanlangan, chunki u professional desktop oynalar, jadvallar, dialoglar va formalarni yaratish imkonini beradi.",
            "Ma'lumotlar SQLite bazasida saqlanadi. SQLite alohida server talab qilmaydi va bitta fayl ko'rinishida ishlaydi.",
        ],
        ["Python 3", "PyQt6", "SQLite", "QTableWidget", "QDialog", "QComboBox", "QSpinBox"],
    ),
    (
        "7-bet. Dastur arxitekturasi",
        [
            "Dastur uch asosiy qatlamga bo'linadi: interfeys qatlami, biznes mantiq qatlami va ma'lumotlar bazasi qatlami.",
            "Interfeys qatlami ui papkasida joylashgan. Database.py fayli esa ma'lumotlarni saqlash, o'qish va yangilash vazifasini bajaradi.",
            "Bunday tuzilma kodni tartibli saqlashga yordam beradi va keyingi kengaytirishlarni osonlashtiradi.",
        ],
        ["main.py - dastur kirish nuqtasi", "database.py - SQLite so'rovlari", "ui/ - barcha oynalar", "data/market_pos.db - lokal baza fayli"],
    ),
    (
        "8-bet. Ma'lumotlar bazasi jadvallari",
        [
            "Ma'lumotlar bazasida mahsulotlar, foydalanuvchilar, sotuvlar, sotuv elementlari, mijozlar, ta'minotchilar, xarajatlar va ombor harakatlari saqlanadi.",
            "Har bir jadval alohida jarayonni aks ettiradi. Masalan, sales jadvali chek ma'lumotlarini, sale_items esa chek ichidagi mahsulotlarni saqlaydi.",
            "Bunday ajratish sotuv arxivini aniq yuritish va mahsulot qaytarishni to'g'ri hisoblash imkonini beradi.",
        ],
        ["users", "products", "categories", "customers", "sales", "sale_items", "stock_movements", "suppliers", "expenses"],
    ),
    (
        "9-bet. Login moduli",
        [
            "Login moduli foydalanuvchini tizimga kiritish uchun xizmat qiladi. Username va parol kiritilgandan keyin baza orqali tekshiriladi.",
            "Parollar xavfsiz hash ko'rinishida saqlanadi. Bu oddiy matnli parol saqlashga qaraganda ancha xavfsiz hisoblanadi.",
            "Muvaffaqiyatli kirish login_logs jadvaliga yoziladi, keyinchalik admin kim qachon kirganini ko'rishi mumkin.",
        ],
        ["Username kiritish", "Parol kiritish", "Parolni hash orqali tekshirish", "Login tarixini yozish"],
    ),
    (
        "10-bet. Asosiy oyna",
        [
            "Asosiy oyna chap yon menyu va o'ng tomondagi ish maydonidan iborat. Admin uchun barcha menyular, kassir uchun esa faqat zarur bo'lgan oynalar ko'rsatiladi.",
            "Yon menyuda Sotuv, Mahsulotlar, Ombor, Mijozlar, Hisobotlar, Qarzlar, Harajatlar, Kassirlar va Kirish tarixi bo'limlari mavjud.",
            "Pastki qismda foydalanuvchi nomi va Chiqish tugmasi joylashadi. Chiqish orqali boshqa foydalanuvchi bilan qayta kirish mumkin.",
        ],
        ["Navigatsiya menyusi", "Sahifa sarlavhasi", "Soat va sana", "Foydalanuvchi bloki", "Chiqish tugmasi"],
    ),
    (
        "11-bet. Mahsulotlar moduli",
        [
            "Mahsulotlar moduli do'kondagi barcha tovarlarni boshqarish uchun ishlatiladi.",
            "Admin mahsulot nomi, shtrix-kod, kategoriya, ta'minotchi, sotish narxi, xarid narxi va qoldiqni kiritishi mumkin.",
            "Minimal miqdor va o'lchov birligi foydalanuvchi oynasidan olib tashlangan, chunki ular amaliy ish jarayonida ortiqcha maydon hisoblanadi.",
        ],
        ["Mahsulot qo'shish", "Mahsulot tahrirlash", "Mahsulot o'chirish", "Shtrix-kod bilan ishlash", "Kategoriya va ta'minotchi tanlash"],
    ),
    (
        "12-bet. Mahsulot kategoriyalari",
        [
            "Mahsulotlar bo'limida alohida Kategoriyalar tugmasi mavjud. Bu oynada kategoriya qo'shish, tahrirlash va o'chirish mumkin.",
            "Kategoriya o'chirilganda mahsulotlar o'chmaydi, faqat ularning kategoriya maydoni bo'shatiladi.",
            "Bu yondashuv ma'lumotlar xavfsizligini oshiradi, chunki kategoriya bilan birga mahsulotlar yo'qolib ketmaydi.",
        ],
        ["Kategoriya yaratish", "Kategoriya nomini tahrirlash", "Kategoriya o'chirish", "Mahsulotlarni saqlab qolish"],
    ),
    (
        "13-bet. Mahsulot template moduli",
        [
            "Template moduli turli mahsulotlar uchun qo'shimcha xususiyat maydonlarini yaratish imkonini beradi.",
            "Masalan, telefon mahsuloti uchun brend, model, rang kabi xususiyatlar kerak bo'lishi mumkin.",
            "Template tanlanganda mahsulot formasi avtomatik ravishda shu xususiyat maydonlarini chiqaradi.",
        ],
        ["Template yaratish", "Xususiyatlar qo'shish", "Majburiy maydon belgilash", "Template tahrirlash", "Template o'chirish"],
    ),
    (
        "14-bet. Sotuv moduli",
        [
            "Sotuv moduli kassirning asosiy ish oynasi hisoblanadi. Kassir mahsulotni qidiradi, savatga qo'shadi va sotuvni yakunlaydi.",
            "Sotuv yakunlanganda mahsulot qoldig'i avtomatik kamayadi. Agar qoldiq yetarli bo'lmasa, tizim sotuvni o'tkazmaydi.",
            "Berildi va qaytim maydonlari olib tashlangan. Naqd va plastik savdolar avtomatik to'liq to'langan deb saqlanadi.",
        ],
        ["Mahsulot qidirish", "Savatga qo'shish", "Miqdor tanlash", "Chegirma berish", "To'lov turini tanlash", "Sotuvni yakunlash"],
    ),
    (
        "15-bet. To'lov turlari",
        [
            "Tizimda uch asosiy to'lov turi mavjud: naqd, plastik karta va qarz.",
            "Naqd va plastik kartada sotuv summa to'langan deb hisoblanadi. Qarz savdo uchun mijoz tanlash majburiy.",
            "Qarz savdo qilinganda mijoz balansiga sotuv summasi qo'shiladi va keyinchalik mijoz qarzi sifatida ko'rinadi.",
        ],
        ["Naqd", "Plastik karta", "Qarz", "Mijoz balansi", "Valyuta kursi bilan hisoblash"],
    ),
    (
        "16-bet. Sotuv arxivi",
        [
            "Mahsulotlar bo'limidagi Arxiv oynasi sotilgan mahsulotlar tarixini ko'rsatadi.",
            "Arxivda har bir mahsulot qachon sotilgani, qaysi chekda bo'lgani, kim sotgani, mijoz kimligi va to'lov turi ko'rsatiladi.",
            "Bu bo'lim keyinchalik mahsulot qaytarish jarayoni uchun asosiy nazorat oynasi hisoblanadi.",
        ],
        ["Sotuv raqami", "Sotilgan sana", "Mahsulot nomi", "Shtrix-kod", "Kassir", "Mijoz", "To'lov turi"],
    ),
    (
        "17-bet. Mahsulot qaytarish jarayoni",
        [
            "Mijoz mahsulotni qaytarib bergan holatda admin yoki kassir Arxiv oynasidan tegishli sotuvni topadi.",
            "Qaytarish tugmasi bosilganda qaytariladigan miqdor kiritiladi. Tizim qaytarilgan miqdorni sotilgan miqdordan oshirmaydi.",
            "Qaytarish bajarilgandan keyin mahsulot qoldig'i avtomatik oshadi va arxiv oynasi yangilanadi.",
        ],
        ["Arxivdan mahsulot topish", "Qaytarish tugmasini bosish", "Miqdor kiritish", "Omborga qaytarish", "Arxivni yangilash"],
    ),
    (
        "18-bet. Soft-delete mexanizmi",
        [
            "Mahsulot o'chirilganda u bazadan butunlay o'chirilmaydi, balki yashiriladi. Bu soft-delete deb ataladi.",
            "Bunday yondashuv sotuv tarixini saqlab qoladi. Agar mahsulot bazadan fizik o'chirilsa, eski arxivdagi bog'lanishlar buzilishi mumkin.",
            "Agar yashirilgan mahsulot keyinchalik arxivdan qaytarilsa, u yana mahsulotlar ro'yxatida paydo bo'ladi.",
        ],
        ["Mahsulot ro'yxatdan yashiriladi", "Tarix saqlanadi", "Arxiv ishlashda davom etadi", "Qaytarishda mahsulot qayta aktiv bo'ladi"],
    ),
    (
        "19-bet. Ombor moduli",
        [
            "Ombor moduli mahsulot qoldig'ini nazorat qilish va kirim qo'shish uchun xizmat qiladi.",
            "Mahsulot sotilganda qoldiq kamayadi, kirim qilinganda oshadi, qaytarilganda ham omborga qaytadi.",
            "Ombor harakatlari stock_movements jadvalida saqlanadi, bu esa qoldiq tarixini nazorat qilish imkonini beradi.",
        ],
        ["Kirim qo'shish", "Qoldiq ko'rish", "Kam qoldiqni aniqlash", "Sotuv chiqimini yozish", "Qaytarish kirimini yozish"],
    ),
    (
        "20-bet. Mijozlar moduli",
        [
            "Mijozlar moduli doimiy xaridorlarni ro'yxatga olish va ularning balansini yuritish uchun ishlatiladi.",
            "Balans mijozning do'konga bo'lgan qarzini bildiradi. Qarz savdo qilinganda balans oshadi.",
            "Mahsulot qaytarilganda agar sotuv qarzga qilingan bo'lsa, mijoz balansi avtomatik kamayadi.",
        ],
        ["Mijoz qo'shish", "Telefon raqam saqlash", "Balans ko'rish", "Qarz savdo bilan bog'lash", "Qaytarishda balansni kamaytirish"],
    ),
    (
        "21-bet. Ta'minotchilar va qarzlar",
        [
            "Ta'minotchilar moduli mahsulot kimdan olinganini belgilash va ularga bo'lgan qarzlarni yuritish uchun xizmat qiladi.",
            "Har bir ta'minotchi uchun telefon, izoh, qarz valyutasi, joriy qarz va jami olingan summa saqlanadi.",
            "Qarz qo'shish va to'lov qilish tarixda alohida ko'rsatiladi.",
        ],
        ["Ta'minotchi qo'shish", "Qarz oshirish", "Qarz kamaytirish", "To'lov tarixi", "Valyuta bo'yicha qarz yuritish"],
    ),
    (
        "22-bet. Harajatlar moduli",
        [
            "Harajatlar moduli do'konning kundalik xarajatlarini yuritish uchun mo'ljallangan.",
            "Harajat kategoriya, summa, valyuta va izoh bilan saqlanadi. Kategoriyalar alohida boshqariladi.",
            "Bu modul foyda hisobini aniqroq tahlil qilishga yordam beradi.",
        ],
        ["Harajat qo'shish", "Harajat tahrirlash", "Harajat o'chirish", "Kategoriya boshqarish", "Grafik hisobot"],
    ),
    (
        "23-bet. Hisobotlar moduli",
        [
            "Hisobotlar moduli sotuv, foyda, chek soni va o'rtacha chek kabi ko'rsatkichlarni ko'rsatadi.",
            "Hisobotlar kunlik, haftalik va oylik tahlil uchun ishlatilishi mumkin.",
            "Qaytarilgan mahsulotlar hisob-kitoblarda inobatga olinadi, ya'ni qaytgan miqdor foyda va qoldiqni noto'g'ri ko'rsatib yubormaydi.",
        ],
        ["Umumiy daromad", "Foyda", "Cheklar soni", "O'rtacha chek", "Kassir kesimi", "Mijoz kesimi"],
    ),
    (
        "24-bet. Valyuta bilan ishlash",
        [
            "Tizimda UZS asosiy valyuta hisoblanadi. Bundan tashqari USD, EUR yoki boshqa valyutalar qo'shilishi mumkin.",
            "Mahsulot narxi boshqa valyutada kiritilsa, tizim uni kurs asosida so'mga aylantirib saqlaydi.",
            "Sotuv vaqtida ham valyuta tanlash mumkin, lekin ichki hisob-kitoblar so'mda yuritiladi.",
        ],
        ["UZS asosiy valyuta", "USD va EUR kurslari", "Kursni tahrirlash", "Narxni so'mga aylantirish", "Sotuvda valyuta ko'rsatish"],
    ),
    (
        "25-bet. Shtrix-kod va printer",
        [
            "Mahsulotlarga shtrix-kod biriktirish mumkin. Shtrix-kod orqali mahsulotni tez qidirish va savatga qo'shish imkoniyati yaratiladi.",
            "Tizim Code128 formatida barcode label chizadi va Xprinter kabi label printerlarga chiqarish uchun dialog ochadi.",
            "Bu do'konda skaner bilan ishlash jarayonini ancha tezlashtiradi.",
        ],
        ["Barcode kiritish", "Barcode validatsiyasi", "Label preview", "Printer tanlash", "40x30 mm label"],
    ),
    (
        "26-bet. Xavfsizlik va ma'lumotlar yaxlitligi",
        [
            "Dasturda ma'lumotlar yaxlitligi muhim ahamiyatga ega. Sotuv, qaytarish va ombor harakatlari tranzaksiya orqali bajariladi.",
            "Agar jarayon o'rtasida xatolik yuz bersa, baza eski holatiga qaytariladi. Bu noto'g'ri qoldiq yoki noto'g'ri qarz yozilishining oldini oladi.",
            "Mahsulotni fizik o'chirmasdan yashirish ham ma'lumotlar yaxlitligi uchun tanlangan yechimdir.",
        ],
        ["Parol hash bilan saqlanadi", "Tranzaksiyalar ishlatiladi", "Foreign key bog'lanishlari", "Soft-delete", "User-facing xatoliklar"],
    ),
    (
        "27-bet. Testlash rejasi",
        [
            "Loyiha ishlab chiqilgandan keyin har bir modul alohida test qilinadi. Avval database funksiyalari, keyin UI oynalari tekshiriladi.",
            "Sotuv testida mahsulot qoldig'i kamayishi, qarz savdoda mijoz balansi oshishi va qaytarishda qoldiq tiklanishi tekshiriladi.",
            "Bundan tashqari admin va kassir rollari alohida ko'rib chiqiladi.",
        ],
        ["Login testi", "Mahsulot CRUD testi", "Sotuv testi", "Qarz savdo testi", "Qaytarish testi", "Hisobot testi"],
    ),
    (
        "28-bet. Foydalanish ketma-ketligi",
        [
            "Dasturdan foydalanish uchun avval admin login qiladi. Keyin mahsulot kategoriyalari, ta'minotchilar va mahsulotlar kiritiladi.",
            "Mahsulotlar bazasi tayyor bo'lgandan keyin kassir sotuv oynasidan savdoni amalga oshiradi.",
            "Agar mijoz mahsulot qaytarsa, Arxiv oynasidan sotuv topilib, Qaytarish tugmasi bosiladi.",
        ],
        ["Admin bilan kirish", "Kategoriyalarni yaratish", "Mahsulotlarni qo'shish", "Sotuv qilish", "Hisobot ko'rish", "Qaytarishni arxivdan bajarish"],
    ),
    (
        "29-bet. Kelajakdagi rivojlantirish imkoniyatlari",
        [
            "Loyiha keyinchalik yanada kengaytirilishi mumkin. Masalan, chekni PDF ko'rinishida chiqarish, Excel import/export va tarmoq orqali ishlash imkoniyati qo'shiladi.",
            "Bulutli zaxira nusxa olish, ko'p filialli savdo va mobil ilova bilan integratsiya ham kelajakdagi imkoniyatlar hisoblanadi.",
            "Bunday rivojlantirishlar tizimni kichik POS dasturdan to'liq savdo boshqaruv platformasiga aylantirishi mumkin.",
        ],
        ["PDF chek", "Excel import/export", "Backup", "Ko'p filial", "Mobil ilova", "Online dashboard"],
    ),
    (
        "30-bet. Xulosa",
        [
            "Market POS individual loyihasi do'kon savdo jarayonlarini avtomatlashtirishga qaratilgan amaliy dastur hisoblanadi.",
            "Loyihada mahsulotlar, sotuv, ombor, mijozlar, qarzlar, harajatlar, hisobotlar, arxiv va qaytarish kabi muhim funksiyalar jamlangan.",
            "Tizimning asosiy afzalligi - sodda interfeys, lokal baza, tez ishlash va real savdo jarayonlariga moslashtirilgan funksionallikdir.",
        ],
        ["Loyiha amaliy ahamiyatga ega", "Kichik biznes uchun qulay", "Savdo va omborni bog'laydi", "Arxiv orqali qaytarish nazorat qilinadi", "Kelajakda kengaytirish mumkin"],
    ),
]


def document_xml():
    body = []
    body.append(p("Market Store POS individual loyiha hujjati", "Title"))
    body.append(p("30 betlik loyiha rejasi va tavsifi", "Subtitle"))
    body.append(p(f"Tayyorlangan sana: {datetime.now().strftime('%d.%m.%Y')}"))
    body.append(p("Loyiha turi: desktop savdo boshqaruv tizimi"))
    for index, (title, paragraphs, items) in enumerate(pages):
        body.append(p(title, "Heading1", break_before=index > 0))
        for paragraph in paragraphs:
            body.append(p(paragraph))
        body.append(p("Reja va bajariladigan ishlar:", "Heading2"))
        body.append(bullets(items))
    body.append(
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(body)}</w:body>
</w:document>'''


content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''

styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="28"/></w:rPr>
    <w:pPr><w:spacing w:after="160" w:line="360" w:lineRule="auto"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:rPr><w:b/><w:sz w:val="44"/></w:rPr>
    <w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:rPr><w:i/><w:sz w:val="32"/></w:rPr>
    <w:pPr><w:jc w:val="center"/><w:spacing w:after="260"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:rPr><w:b/><w:sz w:val="34"/></w:rPr>
    <w:pPr><w:spacing w:before="120" w:after="220"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:rPr><w:b/><w:sz w:val="28"/></w:rPr>
    <w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>
  </w:style>
</w:styles>'''

core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Market POS individual loyiha</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:modified>
</cp:coreProperties>'''

app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Microsoft Word</Application>
</Properties>'''


with ZipFile(OUT, "w", ZIP_DEFLATED) as docx:
    docx.writestr("[Content_Types].xml", content_types)
    docx.writestr("_rels/.rels", rels)
    docx.writestr("word/_rels/document.xml.rels", doc_rels)
    docx.writestr("word/document.xml", document_xml())
    docx.writestr("word/styles.xml", styles)
    docx.writestr("docProps/core.xml", core)
    docx.writestr("docProps/app.xml", app)

print(OUT.resolve())
