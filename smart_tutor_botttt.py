#!/usr/bin/env python3
"""
🧠 AQLLI SHAXSIY USTOZ BOT
- Onboarding: ism, yosh, qiziqishlar, bilim darajasi, qulay vaqt
- Har kuni belgilangan vaqtda motivatsion xabar
- Darajaga qarab adaptiv darslar
- Claude AI bilan suhbat (sevimli mavzuda o'rgatadi)
- Shaxsiy jadval tuzish
- SQLite database
"""

import os
import logging
import sqlite3
import random
import asyncio
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import anthropic
from gtts import gTTS

# ==================== SOZLAMALAR ====================
BOT_TOKEN = ""       # @BotFather dan oling
ANTHROPIC_API_KEY = "" # console.anthropic.com dan oling

# Onboarding bosqichlari
(
    ASK_NAME, ASK_AGE, ASK_INTERESTS, ASK_LANG,
    ASK_LEVEL, ASK_NOTIFY_TIME, CONFIRM_PROFILE, CHATTING
) = range(8)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== KONSTANTALAR ====================

LANGUAGES = {
    "en": "🇬🇧 Ingliz tili",
    "ru": "🇷🇺 Rus tili",
    "de": "🇩🇪 Nemis tili",
    "fr": "🇫🇷 Fransuz tili",
    "ko": "🇰🇷 Koreyscha",   # ← YANGI QO'SHILDI
}

INTERESTS = {
    "sport": "⚽ Sport",
    "music": "🎵 Musiqa",
    "tech": "💻 Texnologiya",
    "cooking": "🍳 Oshpazlik",
    "travel": "✈️ Sayohat",
    "business": "💼 Biznes",
    "art": "🎨 San'at",
    "science": "🔬 Fan",
}

LEVELS = {
    "beginner": ("🌱 Boshlang'ich (0–30%)", 15),
    "elementary": ("📗 Oddiy (30–50%)", 35),
    "intermediate": ("📘 O'rta (50–70%)", 60),
    "upper": ("📙 Yuqori o'rta (70–85%)", 77),
    "advanced": ("🏆 Yuqori (85–100%)", 92),
}

MOTIVATIONS = [
    "🔥 Bugun bir yangi so'z o'rgansang, ertaga ikki yangi dunyo ochiladi!",
    "💪 Har bir dars seni maqsadingga bir qadam yaqinlashtiradi!",
    "🌟 Zo'r! Sen har kuni o'sib borayapsan!",
    "🎯 Muvaffaqiyat — bu kichik qadamlarning yig'indisi. Davom et!",
    "🚀 Bugun o'rgan, ertaga qo'lla — shu oddiy formula!",
    "🧠 Miyangni mashq qildirish — eng yaxshi investitsiya!",
    "⭐ Sen buni qila olasan. Har kuni ozgina — katta natija!",
    "🌈 Yangi til = yangi hayot imkoniyatlari!",
]

# ==================== DATABASE ====================

def init_db():
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            age         INTEGER,
            interests   TEXT,
            lang        TEXT DEFAULT 'en',
            level       TEXT DEFAULT 'beginner',
            level_pct   INTEGER DEFAULT 15,
            notify_hour INTEGER DEFAULT 9,
            notify_min  INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            streak      INTEGER DEFAULT 0,
            last_active TEXT,
            onboarded   INTEGER DEFAULT 0,
            joined_date TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            session_type TEXT,
            score       INTEGER DEFAULT 0,
            date        TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER,
            role     TEXT,
            content  TEXT,
            date     TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(user_id, **kwargs):
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    vals = list(kwargs.values())
    c.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    sets = ", ".join([f"{k}=?" for k in kwargs.keys()])
    c.execute(f"UPDATE users SET {sets} WHERE user_id=?", vals + [user_id])
    conn.commit()
    conn.close()

def save_chat(user_id, role, content):
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO chat_history (user_id, role, content, date) VALUES (?,?,?,?)",
        (user_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_chat_history(user_id, limit=10):
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def get_all_notifiable_users():
    conn = sqlite3.connect("smart_tutor.db")
    c = conn.cursor()
    c.execute("SELECT user_id, notify_hour, notify_min FROM users WHERE onboarded=1")
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== AI USTOZ ====================

def build_system_prompt(user_row):
    _, username, full_name, age, interests_str, lang, level, level_pct, *_ = user_row
    lang_name = LANGUAGES.get(lang, lang)
    interests_list = interests_str.split(",") if interests_str else []
    interest_names = [INTERESTS.get(i, i) for i in interests_list]

    # Korean uchun maxsus ko'rsatma
    extra_lang_note = ""
    if lang == "ko":
        extra_lang_note = (
            "\nKOREYSCHA O'QITISH QOIDALARI:\n"
            "- Har doim: Hangul (한글) + romanizatsiya + o'zbek tarjimasini birga ber\n"
            "- Misol: 안녕하세요 (Annyeonghaseyo) — Salom (rasmiy)\n"
            "- Boshlang'ich darajada faqat oddiy so'zlashuvdan boshlang\n"
            "- Honorifik (rasmiy/norasmiy) farqini darslar davomida tushuntir\n"
        )

    return f"""Sen {full_name} ismli foydalanuvchining shaxsiy til ustozisan.

FOYDALANUVCHI PROFILI:
- Ism: {full_name}
- Yosh: {age}
- O'rganayotgan til: {lang_name}
- Bilim darajasi: {level_pct}% ({level})
- Qiziqishlari: {', '.join(interest_names) if interest_names else 'umumiy'}
{extra_lang_note}
SEN QANDAY O'RGATASAN:
1. Har doim O'ZBEK tilida gapirasiz (tushuntirish uchun), lekin {lang_name} so'z va jumlalarni o'rgatasan
2. Darajaga moslash: {level_pct}% bilsa, {"juda oddiy so'z va jumlalar" if level_pct < 40 else "o'rta daraja jumlalar" if level_pct < 70 else "murakkab grammatika va iboralar"} ishlatasan
3. Qiziqishlariga mos misollar berasan: {', '.join(interest_names) if interest_names else 'umumiy mavzular'}
4. Har xabardan so'ng BITTA yangi so'z yoki ibora o'rgat
5. Agar to'g'ri qilsa — maqta, noto'g'ri bo'lsa — mehrli tuzat
6. Jadval taklif qilganda: haftaning qaysi kuni nima o'rganilishini aniqlash
7. Qisqa, do'stona, energik bo'l. Hech qachon zerikarli bo'lma!
8. Emoji ishlatishni yaxshi ko'rasan 😊

Suhbatni {full_name} ning qiziqishlaridan misol keltirgan holda boshlang!"""

async def ask_ai(user_row, user_message: str, history: list) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = build_system_prompt(user_row)

    messages = []
    for role, content in history[-8:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=600,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"AI xato: {e}")
        return "⚠️ AI bilan muammo yuz berdi. Iltimos, qayta urinib ko'ring."

# ==================== ONBOARDING ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = get_user(user.id)

    if existing and existing[13] == 1:  # onboarded
        await show_main_menu(update, context)
        return ConversationHandler.END

    upsert_user(user.id, username=user.username or "", joined_date=datetime.now().strftime("%Y-%m-%d"))

    await update.message.reply_text(
        f"👋 Salom! Men sizning *shaxsiy til ustozingizman!* 🎓\n\n"
        f"Men siz bilan *har kuni* ishlashga tayyorman:\n"
        f"✅ Darajangizga mos darslar\n"
        f"⏰ Siz tanlagan vaqtda eslatma\n"
        f"🤖 AI bilan jonli suhbat\n"
        f"📊 Shaxsiy o'quv jadvali\n\n"
        f"Keling, bir-birimizni taniymiz! 🤝\n\n"
        f"*Ismingiz nima?*",
        parse_mode="Markdown"
    )
    return ASK_NAME

async def ask_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Iltimos, to'liq ismingizni yozing.")
        return ASK_NAME

    context.user_data["full_name"] = name
    await update.message.reply_text(
        f"Juda yaxshi, *{name}*! 😊\n\n"
        f"*Yoshingiz nechechi?* (Raqam yozing, masalan: 18)",
        parse_mode="Markdown"
    )
    return ASK_AGE

async def ask_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text.strip())
        if not (5 <= age <= 99):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Iltimos, to'g'ri yosh kiriting (5 dan 99 gacha).")
        return ASK_AGE

    context.user_data["age"] = age
    keyboard = []
    row = []
    for code, name in INTERESTS.items():
        row.append(InlineKeyboardButton(name, callback_data=f"int_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✅ Tayyor", callback_data="int_done")])

    context.user_data["selected_interests"] = []
    await update.message.reply_text(
        f"Ajoyib! {age} — o'rganishga ajoyib yosh! 💪\n\n"
        f"*Qiziqishlaringizni tanlang* (bir nechta bo'lishi mumkin):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_INTERESTS

async def interest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "int_done":
        selected = context.user_data.get("selected_interests", [])
        if not selected:
            await query.answer("Kamida 1 ta tanlang!", show_alert=True)
            return ASK_INTERESTS

        keyboard = []
        for code, name in LANGUAGES.items():
            keyboard.append([InlineKeyboardButton(name, callback_data=f"lang_{code}")])

        await query.edit_message_text(
            f"✅ Ajoyib tanlov!\n\n*Qaysi tilni o'rganmoqchisiz?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_LANG

    code = data.replace("int_", "")
    selected = context.user_data.get("selected_interests", [])
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    context.user_data["selected_interests"] = selected

    keyboard = []
    row = []
    for icode, iname in INTERESTS.items():
        checked = "✅ " if icode in selected else ""
        row.append(InlineKeyboardButton(f"{checked}{iname}", callback_data=f"int_{icode}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(f"✅ Tayyor ({len(selected)} ta tanlangan)", callback_data="int_done")])

    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_INTERESTS

async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.replace("lang_", "")
    context.user_data["lang"] = lang

    keyboard = []
    for lkey, (lname, _) in LEVELS.items():
        keyboard.append([InlineKeyboardButton(lname, callback_data=f"lvl_{lkey}")])

    await query.edit_message_text(
        f"🌟 {LANGUAGES[lang]} — ajoyib tanlov!\n\n"
        f"*Hozirgi bilim darajangiz qanday?*\n"
        f"_(Halollik bilan tanlang — shunda sizga mos darslar beriladi)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_LEVEL

async def level_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    level = query.data.replace("lvl_", "")
    level_name, level_pct = LEVELS[level]
    context.user_data["level"] = level
    context.user_data["level_pct"] = level_pct

    keyboard = []
    for hour in [7, 8, 9, 10, 18, 19, 20, 21]:
        label = f"🕐 {hour:02d}:00"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"time_{hour}_0")])

    await query.edit_message_text(
        f"✅ {level_name} — tushundim!\n\n"
        f"⏰ *Har kuni qaysi vaqtda eslatma olishni xohlaysiz?*\n"
        f"_(Men o'sha vaqtda sizni o'rganishga chaqiraman)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_NOTIFY_TIME

async def time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.replace("time_", "").split("_")
    hour, minute = int(parts[0]), int(parts[1])
    context.user_data["notify_hour"] = hour
    context.user_data["notify_min"] = minute

    # Profilni saqlash
    user_id = query.from_user.id
    ud = context.user_data
    upsert_user(
        user_id,
        full_name=ud.get("full_name", ""),
        age=ud.get("age", 0),
        interests=",".join(ud.get("selected_interests", [])),
        lang=ud.get("lang", "en"),
        level=ud.get("level", "beginner"),
        level_pct=ud.get("level_pct", 15),
        notify_hour=hour,
        notify_min=minute,
        onboarded=1,
        last_active=datetime.now().isoformat()
    )

    lang_name = LANGUAGES.get(ud.get("lang", "en"), "")
    level_name = LEVELS.get(ud.get("level", "beginner"), ("",))[0]
    interest_names = [INTERESTS.get(i, i) for i in ud.get("selected_interests", [])]

    await query.edit_message_text(
        f"🎉 *Profil tayyor!*\n\n"
        f"👤 Ism: *{ud.get('full_name')}*\n"
        f"🎂 Yosh: *{ud.get('age')}*\n"
        f"🌍 Til: *{lang_name}*\n"
        f"📊 Daraja: *{level_name}*\n"
        f"❤️ Qiziqishlar: *{', '.join(interest_names)}*\n"
        f"⏰ Eslatma: *Har kuni soat {hour:02d}:00 da*\n\n"
        f"Endi boshlaylik! 🚀",
        parse_mode="Markdown"
    )

    await asyncio.sleep(1)
    await show_main_menu(update, context, via_query=True)
    return ConversationHandler.END

# ==================== ASOSIY MENYU ====================

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, via_query=False):
    user_id = update.effective_user.id
    user = get_user(user_id)
    name = user[2] if user else "Do'stim"
    level_pct = user[7] if user else 15

    keyboard = [
        [InlineKeyboardButton("🤖 AI Ustoz bilan suhbat", callback_data="start_chat")],
        [InlineKeyboardButton("📚 Bugungi dars", callback_data="daily_lesson"),
         InlineKeyboardButton("✅ Quiz", callback_data="start_quiz")],
        [InlineKeyboardButton("📅 Jadval tuzish", callback_data="make_schedule")],
        [InlineKeyboardButton("📊 Mening profilim", callback_data="my_profile")],
        [InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings")],
    ]

    text = (
        f"🏠 *Bosh Menyu* — Salom, {name}! 👋\n\n"
        f"📊 Daraja: *{level_pct}%*  {'🌱' if level_pct < 40 else '📘' if level_pct < 70 else '🏆'}\n"
        f"💡 Bugun ham o'rganishni davom ettiring!\n"
    )

    if via_query and update.callback_query:
        await update.callback_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ==================== AUDIO TTS ====================

# Har bir til uchun gTTS kodi
LANG_TTS_CODE = {
    "en": "en",
    "ru": "ru",
    "de": "de",
    "fr": "fr",
    "ko": "ko",
}

async def send_audio_pronunciation(update_or_query, context, word: str, lang: str):
    """So'zni ovozli o'qib Telegram audio sifatida yuboradi."""
    tts_lang = LANG_TTS_CODE.get(lang, "en")
    audio_file = f"tts_{context.bot.id}_{lang}.mp3"
    try:
        tts = gTTS(text=word, lang=tts_lang)
        tts.save(audio_file)

        # callback_query yoki oddiy message bo'lishi mumkin
        if hasattr(update_or_query, "message"):
            chat_id = update_or_query.message.chat_id
        else:
            chat_id = update_or_query.from_user.id

        with open(audio_file, "rb") as f:
            await context.bot.send_voice(
                chat_id=chat_id,
                voice=f,
                caption=f"🔊 *{word}* talaffuzi",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"TTS xato: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Audio yuklanmadi. Qayta urinib ko'ring."
        )
    finally:
        if os.path.exists(audio_file):
            os.remove(audio_file)

# ==================== DAILY LESSON ====================

LESSON_CONTENT = {
    "beginner": [
        # Ingliz tili
        ("Hello", "Salom", "Hello! My name is Ali.", "Greeting (Salomlashish)"),
        ("Thank you", "Rahmat", "Thank you very much!", "Politeness (Odob)"),
        ("Yes / No", "Ha / Yo'q", "Yes, I understand. No, I don't.", "Basic answers"),
        ("Good morning", "Xayrli tong", "Good morning, teacher!", "Greetings"),
        ("My name is...", "Mening ismim...", "My name is Kamola.", "Introduction"),
        # Koreyscha
        ("안녕하세요 (Annyeonghaseyo)", "Salom (rasmiy)", "안녕하세요! 저는 Ali예요.", "Korean — Salomlashish"),
        ("감사합니다 (Gamsahamnida)", "Rahmat (rasmiy)", "감사합니다! 정말요.", "Korean — Odob"),
        ("네 / 아니요 (Ne / Aniyo)", "Ha / Yo'q", "네, 알아요. 아니요, 몰라요.", "Korean — Asosiy javoblar"),
        ("제 이름은... (Je ireumeun...)", "Mening ismim...", "제 이름은 Kamola예요.", "Korean — Tanishish"),
        ("안녕히 가세요 (Annyeonghi gaseyo)", "Xayr (ketayotganga)", "안녕히 가세요! 또 봐요.", "Korean — Xayrlashish"),
    ],
    "elementary": [
        # Ingliz tili
        ("I would like", "Men xohlayman", "I would like some water.", "Desire expression"),
        ("How much?", "Qancha turadi?", "How much is this book?", "Shopping"),
        ("Where is...?", "...qayerda?", "Where is the station?", "Directions"),
        ("I don't understand", "Tushunmadim", "Sorry, I don't understand.", "Communication"),
        # Koreyscha
        ("주세요 (Juseyo)", "Bering / Iltimos", "물 주세요. (Suv bering.)", "Korean — So'rash"),
        ("얼마예요? (Eolmayeyo?)", "Qancha turadi?", "이 책 얼마예요?", "Korean — Xarid"),
        ("어디예요? (Eodiyeyo?)", "Qayerda?", "화장실 어디예요?", "Korean — Yo'nalish"),
        ("모르겠어요 (Moreugesseoyo)", "Bilmadim / Tushunmadim", "죄송해요, 모르겠어요.", "Korean — Muloqot"),
    ],
    "intermediate": [
        # Ingliz tili
        ("Nevertheless", "Shunga qaramay", "Nevertheless, he continued.", "Contrast"),
        ("In addition", "Bundan tashqari", "In addition, we need more time.", "Addition"),
        ("As a result", "Natijada", "As a result, they succeeded.", "Cause-effect"),
        # Koreyscha
        ("그럼에도 불구하고 (Geureomedo bulguhago)", "Shunga qaramay", "그럼에도 불구하고 계속했어요.", "Korean — Qarama-qarshilik"),
        ("게다가 (Gedaga)", "Bundan tashqari", "게다가 시간이 더 필요해요.", "Korean — Qo'shimcha"),
        ("결과적으로 (Gyeolgwajeogeuro)", "Natijada", "결과적으로 성공했어요.", "Korean — Sabab-natija"),
    ],
    "upper": [
        # Ingliz tili
        ("Notwithstanding", "Bunga qaramadan", "Notwithstanding the risks...", "Formal contrast"),
        ("Subsequently", "Keyinchalik", "Subsequently, the policy changed.", "Sequence"),
        # Koreyscha
        ("~에도 불구하고 (~edo bulguhago)", "~ga qaramadan (rasmiy)", "위험에도 불구하고...", "Korean — Rasmiy qarama-qarshilik"),
        ("이후에 (Ihu-e)", "Keyinchalik", "이후에 정책이 바뀌었어요.", "Korean — Ketma-ketlik"),
    ],
    "advanced": [
        # Ingliz tili
        ("Juxtaposition", "Yonma-yon qo'yish", "The juxtaposition of ideas.", "Rhetoric"),
        ("Elicit", "Uyg'otmoq/chiqarmoq", "To elicit a response.", "Academic"),
        # Koreyscha
        ("병치 (Byeongchi)", "Yonma-yon qo'yish", "생각의 병치가 흥미롭다.", "Korean — Ritorika"),
        ("유발하다 (Yubalhadda)", "Uyg'otmoq", "반응을 유발하다.", "Korean — Akademik"),
    ],
}

async def daily_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    level = user[6] if user else "beginner"
    lang = user[5] if user else "en"
    lang_name = LANGUAGES.get(lang, "")
    chat_id = query.message.chat_id

    # Yangi so'zga o'tganda eski audioni o'chir
    old_audio_id = context.user_data.get("last_audio_message_id")
    if old_audio_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_audio_id)
        except Exception:
            pass
        context.user_data["last_audio_message_id"] = None

    all_lessons = LESSON_CONTENT.get(level, LESSON_CONTENT["beginner"])

    # Faqat tanlangan tilga mos darslarni filter qilish
    if lang == "ko":
        lessons = [l for l in all_lessons if "Korean" in l[3]]
        if not lessons:
            lessons = all_lessons
    else:
        lessons = [l for l in all_lessons if "Korean" not in l[3]]
        if not lessons:
            lessons = all_lessons

    lesson = random.choice(lessons)
    word, translation, example, topic = lesson

    keyboard = [
        [InlineKeyboardButton("✅ Bilaman!", callback_data="lesson_know"),
         InlineKeyboardButton("🔄 Yana bir marta", callback_data="daily_lesson")],
        [InlineKeyboardButton("🔊 Talaffuzni eshit", callback_data="lesson_audio")],
        [InlineKeyboardButton("🤖 AI dan tushuntirish so'ra", callback_data="ask_ai_lesson")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    context.user_data["current_lesson_word"] = word
    context.user_data["current_lesson_lang"] = lang

    text = (
        f"📚 *BUGUNGI DARS* — {lang_name}\n"
        f"{'─' * 30}\n\n"
        f"📌 Mavzu: _{topic}_\n\n"
        f"🔤 *{word}*\n"
        f"🇺🇿 {translation}\n\n"
        f"📝 *Misol:*\n`{example}`\n\n"
        f"💡 Bu so'zni bugun 3 marta ishlating!"
    )

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def lesson_audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dars so'zini TTS bilan ovozli yuboradi. Eski audioni o'chiradi."""
    query = update.callback_query
    await query.answer("🔊 Audio tayyorlanmoqda...")

    word = context.user_data.get("current_lesson_word", "")
    lang = context.user_data.get("current_lesson_lang", "en")
    chat_id = query.message.chat_id

    if not word:
        await query.answer("So'z topilmadi, avval darsni oching!", show_alert=True)
        return

    # Eski audioni o'chirish
    old_audio_id = context.user_data.get("last_audio_message_id")
    if old_audio_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_audio_id)
        except Exception:
            pass  # O'chirilgan bo'lsa e'tibor berma
        context.user_data["last_audio_message_id"] = None

    # Yangi audio yuborish
    tts_lang = LANG_TTS_CODE.get(lang, "en")
    audio_file = f"tts_{chat_id}.mp3"
    try:
        tts = gTTS(text=word, lang=tts_lang)
        tts.save(audio_file)

        with open(audio_file, "rb") as f:
            sent = await context.bot.send_voice(
                chat_id=chat_id,
                voice=f,
                caption=f"🔊 *{word}* — talaffuzi\n_(Yangi so'zga o'tganda bu o'chadi)_",
                parse_mode="Markdown"
            )
        # Yuborilgan audio message_id ni saqla
        context.user_data["last_audio_message_id"] = sent.message_id

    except Exception as e:
        logger.error(f"TTS xato: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Audio yuklanmadi. Qayta urinib ko'ring."
        )
    finally:
        if os.path.exists(audio_file):
            os.remove(audio_file)

# ==================== QUIZ ====================

QUIZ_DATA = {
    "beginner": [
        # Ingliz tili
        ("What does 'apple' mean?", "olma", ["uy", "olma", "suv", "kitob"], "en"),
        ("What does 'book' mean?", "kitob", ["non", "kitob", "eshik", "deraza"], "en"),
        ("What does 'water' mean?", "suv", ["olov", "havo", "suv", "tuproq"], "en"),
        ("What does 'cat' mean?", "mushuk", ["it", "qush", "mushuk", "baliq"], "en"),
        ("What does 'house' mean?", "uy", ["uy", "yo'l", "tog'", "daryo"], "en"),
        # Koreyscha
        ("'사과 (sagwa)' nima degan ma'no?", "olma", ["uy", "olma", "suv", "kitob"], "ko"),
        ("'책 (chaek)' nima degan ma'no?", "kitob", ["non", "kitob", "eshik", "deraza"], "ko"),
        ("'물 (mul)' nima degan ma'no?", "suv", ["olov", "havo", "suv", "tuproq"], "ko"),
        ("'고양이 (goyangi)' nima degan ma'no?", "mushuk", ["it", "qush", "mushuk", "baliq"], "ko"),
        ("'집 (jip)' nima degan ma'no?", "uy", ["uy", "yo'l", "tog'", "daryo"], "ko"),
    ],
    "intermediate": [
        # Ingliz tili
        ("What does 'nevertheless' mean?", "shunga qaramay", ["chunki", "shuning uchun", "shunga qaramay", "va"], "en"),
        ("What does 'moreover' mean?", "bundan tashqari", ["bundan tashqari", "lekin", "agarda", "chunki"], "en"),
        ("What does 'although' mean?", "garchi", ["garchi", "chunki", "va", "yoki"], "en"),
        # Koreyscha
        ("'그럼에도 불구하고' nima degan ma'no?", "shunga qaramay",
         ["chunki", "shuning uchun", "shunga qaramay", "va"], "ko"),
        ("'게다가' nima degan ma'no?", "bundan tashqari",
         ["bundan tashqari", "lekin", "agarda", "chunki"], "ko"),
        ("'결과적으로' nima degan ma'no?", "natijada",
         ["natijada", "lekin", "garchi", "chunki"], "ko"),
    ],
    "advanced": [
        # Ingliz tili
        ("What does 'juxtaposition' mean?", "yonma-yon qo'yish",
         ["ajratish", "birlashtirish", "yonma-yon qo'yish", "o'zgartirish"], "en"),
        ("What does 'elicit' mean?", "uyg'otmoq",
         ["uyg'otmoq", "o'chirmoq", "yashirmoq", "topmoq"], "en"),
        # Koreyscha
        ("'병치 (byeongchi)' nima degan ma'no?", "yonma-yon qo'yish",
         ["ajratish", "birlashtirish", "yonma-yon qo'yish", "o'zgartirish"], "ko"),
        ("'유발하다 (yubalhadda)' nima degan ma'no?", "uyg'otmoq",
         ["uyg'otmoq", "o'chirmoq", "yashirmoq", "topmoq"], "ko"),
    ],
}

async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    level = user[6] if user else "beginner"
    lang = user[5] if user else "en"

    level_key = "intermediate" if level in ("intermediate", "upper") else \
                "advanced" if level == "advanced" else "beginner"
    all_questions = QUIZ_DATA.get(level_key, QUIZ_DATA["beginner"])

    # Faqat tanlangan tilga mos savollar
    questions = [q for q in all_questions if q[3] == lang]
    if not questions:
        questions = all_questions

    q = random.choice(questions)
    question, correct, options = q[0], q[1], q[2][:]

    random.shuffle(options)
    context.user_data["quiz_correct"] = correct
    context.user_data["quiz_question"] = question

    keyboard = [[InlineKeyboardButton(opt, callback_data=f"qans_{opt}")] for opt in options]
    keyboard.append([InlineKeyboardButton("🏠 Menyu", callback_data="back_main")])

    await query.edit_message_text(
        f"✅ *QUIZ VAQTI!*\n{'─'*30}\n\n❓ {question}\n\nTo'g'ri javobni tanlang:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chosen = query.data.replace("qans_", "")
    correct = context.user_data.get("quiz_correct", "")
    user_id = query.from_user.id

    if chosen == correct:
        conn = sqlite3.connect("smart_tutor.db")
        c = conn.cursor()
        c.execute("UPDATE users SET total_score=total_score+10 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        result = f"🎉 *To'g'ri!* +10 ball\n\n✅ Javob: *{correct}*"
    else:
        result = f"❌ *Noto'g'ri!*\n\nTo'g'ri javob: *{correct}*\nSiz: _{chosen}_"

    keyboard = [
        [InlineKeyboardButton("➡️ Keyingi savol", callback_data="start_quiz")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    await query.edit_message_text(result, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== JADVAL ====================

async def make_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)

    if not user:
        await query.answer("Avval /start yozing!", show_alert=True)
        return

    _, username, name, age, interests_str, lang, level, level_pct, notify_h, notify_m, score, *_ = user
    lang_name = LANGUAGES.get(lang, lang)
    level_name = LEVELS.get(level, ("",))[0]

    # Korean uchun maxsus jadval
    if lang == "ko":
        if level_pct < 40:
            schedule = [
                ("Dushanba", "Hangul alifbosi: unlilar va undoshlar (자음/모음)"),
                ("Seshanba", "Asosiy salomlashish iboralari: 안녕하세요, 감사합니다"),
                ("Chorshanba", "Raqamlar: 일, 이, 삼... (1-10)"),
                ("Payshanba", "Mini quiz: Hangul harflari"),
                ("Juma", "Tanishish: 제 이름은... 저는..."),
                ("Shanba", "Haftani takrorlash + AI bilan suhbat"),
                ("Yakshanba", "Dam olish yoki qo'shimcha mashq"),
            ]
        elif level_pct < 70:
            schedule = [
                ("Dushanba", "Grammatika: ~이에요/예요 + 8 yangi so'z"),
                ("Seshanba", "Listening: K-drama klip + Tarjima"),
                ("Chorshanba", "Writing: Kichik paragraf yozish (hangulda)"),
                ("Payshanba", "Quiz + Yangi so'zlar takrorlash"),
                ("Juma", "Grammatika: ~있어요/없어요 + Mashqlar"),
                ("Shanba", "AI bilan erkin suhbat (30 daqiqa)"),
                ("Yakshanba", "Haftani takrorlash + Progress baholash"),
            ]
        else:
            schedule = [
                ("Dushanba", "Murakkab grammatika + TOPIK so'zlar (10 ta)"),
                ("Seshanba", "Maqola o'qish (hangulda) + Tahlil"),
                ("Chorshanba", "Essay yozish hangulda (150+ so'z)"),
                ("Payshanba", "Listening: Native speaker + Takrorlash"),
                ("Juma", "Idiomlar va iboralar (10 ta)"),
                ("Shanba", "AI bilan chuqur mavzu muhokamasi"),
                ("Yakshanba", "TOPIK mock test + Kuchsiz tomonlarni aniqlash"),
            ]
    else:
        # Boshqa tillar uchun avvalgi jadval
        if level_pct < 40:
            schedule = [
                ("Dushanba", "Yangi so'zlar (5 ta) + Talaffuz"),
                ("Seshanba", "Kecha o'rganilgan so'zlarni takrorlash"),
                ("Chorshanba", "Oddiy jumlalar qurilishi"),
                ("Payshanba", "Mini quiz (10 ta savol)"),
                ("Juma", "Salomlashish va tanishish iboralari"),
                ("Shanba", "Haftani takrorlash + AI bilan suhbat"),
                ("Yakshanba", "Dam olish yoki qo'shimcha mashq"),
            ]
        elif level_pct < 70:
            schedule = [
                ("Dushanba", "Grammatika: Present tense + 8 yangi so'z"),
                ("Seshanba", "Listening: Audio mashq + Tarjima"),
                ("Chorshanba", "Writing: Kichik paragraf yozish"),
                ("Payshanba", "Quiz + Yangi so'zlar takrorlash"),
                ("Juma", "Grammar: Past tense + Mashqlar"),
                ("Shanba", "AI bilan erkin suhbat (30 daqiqa)"),
                ("Yakshanba", "Haftani takrorlash + Progress baholash"),
            ]
        else:
            schedule = [
                ("Dushanba", "Murakkab grammatika + Academic so'zlar (10 ta)"),
                ("Seshanba", "Maqola o'qish + Tahlil qilish"),
                ("Chorshanba", "Essay yozish (150+ so'z)"),
                ("Payshanba", "Listening: Native speaker + Takrorlash"),
                ("Juma", "Idiomlar va iboralar (10 ta)"),
                ("Shanba", "AI bilan chuqur mavzu muhokamasi"),
                ("Yakshanba", "Mock test + Kuchsiz tomonlarni aniqlash"),
            ]

    text = (
        f"📅 *{name}ning Shaxsiy O'quv Jadvali*\n"
        f"{'─' * 35}\n"
        f"🌍 Til: {lang_name} | 📊 Daraja: {level_pct}%\n"
        f"⏰ Kunlik dars: *{notify_h:02d}:00*\n"
        f"{'─' * 35}\n\n"
    )

    days_emoji = ["🔵", "🟢", "🟡", "🟠", "🔴", "🟣", "⚪"]
    for i, (day, task) in enumerate(schedule):
        text += f"{days_emoji[i]} *{day}*\n   _{task}_\n\n"

    text += f"{'─' * 35}\n💡 Har kuni soat {notify_h:02d}:00 da eslatma olasiz!"

    keyboard = [
        [InlineKeyboardButton("🤖 AI bilan boshlash", callback_data="start_chat")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== PROFIL ====================

async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    if not user:
        return

    _, username, name, age, interests_str, lang, level, level_pct, notify_h, notify_m, score, streak, *_ = user
    lang_name = LANGUAGES.get(lang, lang)
    level_name = LEVELS.get(level, ("",))[0]
    interest_names = [INTERESTS.get(i, i) for i in (interests_str.split(",") if interests_str else [])]

    progress_bar = "█" * (level_pct // 10) + "░" * (10 - level_pct // 10)

    text = (
        f"👤 *MENING PROFILIM*\n{'─'*30}\n\n"
        f"🏷️ Ism: *{name}*\n"
        f"🎂 Yosh: *{age}*\n"
        f"🌍 Til: *{lang_name}*\n"
        f"❤️ Qiziqishlar: *{', '.join(interest_names)}*\n\n"
        f"📊 *Bilim darajasi:* {level_pct}%\n"
        f"`{progress_bar}` {level_pct}%\n"
        f"🏅 Daraja: {level_name}\n\n"
        f"⭐ Umumiy ball: *{score}*\n"
        f"🔥 Streak: *{streak}* kun\n"
        f"⏰ Eslatma: *{notify_h:02d}:00 har kuni*\n"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Tilni o'zgartirish", callback_data="settings"),
         InlineKeyboardButton("📅 Jadval", callback_data="make_schedule")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== AI SUHBAT ====================

async def start_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    name = user[2] if user else "do'stim"

    context.user_data["in_chat"] = True
    keyboard = [[InlineKeyboardButton("🛑 Suhbatni tugatish", callback_data="end_chat")]]

    await query.edit_message_text(
        f"🤖 *AI Ustoz bilan suhbat*\n{'─'*30}\n\n"
        f"Salom {name}! Men sizning shaxsiy ustozingizman.\n"
        f"Istalgan narsani so'rang — savol, so'z, grammatika, tarjima...\n\n"
        f"_Suhbatni tugatish uchun tugmani bosing._\n\n"
        f"💬 *Xabar yozing:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def chat_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("in_chat"):
        return

    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        return

    user_text = update.message.text
    save_chat(user_id, "user", user_text)

    typing_msg = await update.message.reply_text("✍️ Yozmoqda...")
    history = get_chat_history(user_id)
    response = await ask_ai(user, user_text, history[:-1])
    save_chat(user_id, "assistant", response)

    await typing_msg.delete()
    keyboard = [[InlineKeyboardButton("🛑 Suhbatni tugatish", callback_data="end_chat")]]
    await update.message.reply_text(
        response,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def end_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["in_chat"] = False
    await show_main_menu(update, context, via_query=True)

async def ask_ai_about_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    word = context.user_data.get("current_lesson_word", "bu so'z")
    user = get_user(query.from_user.id)

    context.user_data["in_chat"] = True
    prompt = f"'{word}' so'zi haqida menga batafsil tushuntir. Misollar ber va qanday ishlatishni ko'rsat."

    await query.edit_message_text("✍️ AI javob tayyorlamoqda...")
    history = get_chat_history(query.from_user.id)
    response = await ask_ai(user, prompt, history)
    save_chat(query.from_user.id, "user", prompt)
    save_chat(query.from_user.id, "assistant", response)

    keyboard = [
        [InlineKeyboardButton("💬 Davom ettirish", callback_data="continue_chat")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    await query.edit_message_text(
        f"🤖 *AI Ustoz:*\n\n{response}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def continue_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["in_chat"] = True
    keyboard = [[InlineKeyboardButton("🛑 Tugatish", callback_data="end_chat")]]
    await query.edit_message_text(
        "💬 Davom eting — savolingizni yozing:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== SETTINGS ====================

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🌍 Tilni o'zgartirish", callback_data="change_lang")],
        [InlineKeyboardButton("📊 Darajani o'zgartirish", callback_data="change_level")],
        [InlineKeyboardButton("⏰ Eslatma vaqtini o'zgartirish", callback_data="change_time")],
        [InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
    ]
    await query.edit_message_text(
        "⚙️ *Sozlamalar*\nNimani o'zgartirmoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def change_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(name, callback_data=f"updlang_{code}")] for code, name in LANGUAGES.items()]
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="settings")])
    await query.edit_message_text("🌍 Yangi tilni tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))

async def update_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.replace("updlang_", "")
    upsert_user(query.from_user.id, lang=lang)
    await query.edit_message_text(
        f"✅ Til {LANGUAGES[lang]} ga o'zgartirildi!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]])
    )

async def change_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(lname, callback_data=f"updlvl_{lkey}")] for lkey, (lname, _) in LEVELS.items()]
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="settings")])
    await query.edit_message_text("📊 Yangi darajani tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))

async def update_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    level = query.data.replace("updlvl_", "")
    level_name, level_pct = LEVELS[level]
    upsert_user(query.from_user.id, level=level, level_pct=level_pct)
    await query.edit_message_text(
        f"✅ Daraja {level_name} ga o'zgartirildi!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]])
    )

async def change_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(f"🕐 {h:02d}:00", callback_data=f"updtime_{h}_0")] for h in [7,8,9,10,18,19,20,21]]
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="settings")])
    await query.edit_message_text("⏰ Yangi eslatma vaqtini tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))

async def update_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.replace("updtime_", "").split("_")
    hour, minute = int(parts[0]), int(parts[1])
    upsert_user(query.from_user.id, notify_hour=hour, notify_min=minute)
    await query.edit_message_text(
        f"✅ Eslatma vaqti *{hour:02d}:00* ga o'zgartirildi!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]])
    )

# ==================== KUNLIK ESLATMA ====================

async def send_daily_notifications(app):
    while True:
        now = datetime.now()
        users = get_all_notifiable_users()
        for user_id, notify_h, notify_m in users:
            if now.hour == notify_h and now.minute == notify_m:
                motivation = random.choice(MOTIVATIONS)
                keyboard = [
                    [InlineKeyboardButton("📚 Darsni boshlash", callback_data="daily_lesson")],
                    [InlineKeyboardButton("✅ Quiz", callback_data="start_quiz")],
                    [InlineKeyboardButton("🤖 AI bilan suhbat", callback_data="start_chat")],
                ]
                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"⏰ *Dars vaqti keldi!*\n\n"
                            f"{motivation}\n\n"
                            f"Bugun ham o'rganishni davom ettiramizmi? 💪"
                        ),
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception as e:
                    logger.warning(f"Xabar yuborilmadi {user_id}: {e}")
        await asyncio.sleep(60)

# ==================== CALLBACK ROUTER ====================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    routes = {
        "back_main": lambda: show_main_menu(update, context, via_query=True),
        "daily_lesson": lambda: daily_lesson(update, context),
        "start_quiz": lambda: start_quiz(update, context),
        "make_schedule": lambda: make_schedule(update, context),
        "my_profile": lambda: my_profile(update, context),
        "settings": lambda: settings_menu(update, context),
        "start_chat": lambda: start_chat(update, context),
        "end_chat": lambda: end_chat(update, context),
        "ask_ai_lesson": lambda: ask_ai_about_lesson(update, context),
        "continue_chat": lambda: continue_chat(update, context),
        "lesson_know": lambda: daily_lesson(update, context),
        "lesson_audio": lambda: lesson_audio_handler(update, context),
        "change_lang": lambda: change_lang(update, context),
        "change_level": lambda: change_level(update, context),
        "change_time": lambda: change_time(update, context),
    }

    if data in routes:
        await routes[data]()
    elif data.startswith("qans_"):
        await quiz_answer(update, context)
    elif data.startswith("updlang_"):
        await update_lang(update, context)
    elif data.startswith("updlvl_"):
        await update_level(update, context)
    elif data.startswith("updtime_"):
        await update_time(update, context)

# ==================== MAIN ====================

def main():
    init_db()
    os.environ['APSCHEDULER_TIMEZONE'] = 'Asia/Tashkent'
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Onboarding ConversationHandler
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_handler)],
            ASK_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_age_handler)],
            ASK_INTERESTS: [CallbackQueryHandler(interest_callback, pattern="^int_")],
            ASK_LANG: [CallbackQueryHandler(lang_callback, pattern="^lang_")],
            ASK_LEVEL: [CallbackQueryHandler(level_callback, pattern="^lvl_")],
            ASK_NOTIFY_TIME: [CallbackQueryHandler(time_callback, pattern="^time_")],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", lambda u, c: show_main_menu(u, c)))
    app.add_handler(CommandHandler("help", lambda u, c: u.message.reply_text(
        "📋 *Buyruqlar:*\n/start — Profil va bosh menyu\n/menu — Bosh menyu\n\n"
        "Yoki tugmalardan foydalaning! 👇", parse_mode="Markdown"
    )))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message_handler))

    # Kunlik eslatmalar
    loop = asyncio.get_event_loop()
    loop.create_task(send_daily_notifications(app))

    print("🤖 Aqlli Ustoz Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
