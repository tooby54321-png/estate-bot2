import asyncio
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, InputMediaPhoto
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

TOKEN         = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
AGENT_CHAT_ID = os.getenv("AGENT_CHAT_ID", "YOUR_TELEGRAM_ID_HERE")

bot     = Bot(token=TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ══════════════════════════════════════════════════════════════════
#  ПАРСЕР ФОРМАТУ REALTSOFT CRM
#  Структура рядка (підтверджена аналізом реального експорту):
#
#  [0] status        — active / inactive
#  [1] num           — порядковий номер
#  [2] code          — числовий код (13070)
#  [3] deal_type     — Продаж / Оренда
#  [4..] title       — заголовок до першого опису
#  ...  description  — повний текст опису
#  ...  Ціна: X $    — ціна
#  ...  Код #XXXX    — повторний код
#  ...  район        — Личаківський / Сихів тощо
#  ...  lat lng      — координати (49.xxx 24.xxx)
#  ...  URLs         — фото об'єкту (estate-images/watermark/...)
#  ...  URL          — фото агента (user/...)
#  ...  email        — email агента
#  ...  agency       — назва агентства
#  ...  phone        — телефон агента (0XXXXXXXXX)
#  ...  datetime     — дата створення / оновлення
# ══════════════════════════════════════════════════════════════════

def parse_crm_line(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None

    parts = raw.split()
    if len(parts) < 5:
        return None

    obj = {}

    # ── Фіксовані позиційні поля ──────────────────────────────────
    obj["status"]    = parts[0]                          # active/inactive
    obj["num"]       = parts[1]                          # порядковий №
    obj["code"]      = parts[2]                          # 13070
    obj["deal_type"] = parts[3]                          # Продаж / Оренда

    # ── Фото об'єкту (estate-images/watermark) ───────────────────
    obj["photos"] = re.findall(
        r'https://[^\s]+/estate-images/watermark/[^\s]+\.jpg', raw
    )

    # ── Фото агента ───────────────────────────────────────────────
    agent_photos = re.findall(r'https://[^\s]+/user/[^\s]+\.jpg', raw)
    obj["agent_photo"] = agent_photos[0] if agent_photos else ""

    # ── Ціна ──────────────────────────────────────────────────────
    # \x24 = символ $ (екранування для коректної роботи regex у Python)
    price_m = re.search(r'Ціна:\s*([\d\s]+?)\s*\x24', raw)
    if price_m:
        obj["price_usd"] = int(price_m.group(1).replace(" ", ""))
    else:
        price_m2 = re.search(r'(\d[\d ]+?)\s*\x24', raw)
        obj["price_usd"] = int(price_m2.group(1).replace(" ", "")) if price_m2 else 0

    # ── Код ───────────────────────────────────────────────────────
    code_m = re.search(r'Код\s+#(\d+)', raw)
    if code_m:
        obj["code"] = code_m.group(1)

    # ── Площа ─────────────────────────────────────────────────────
    area_m = re.search(r'Площа:\s*([\d,\.]+)\s*м²', raw)
    obj["area"] = area_m.group(1).replace(",", ".") if area_m else "—"

    # ── Площа кухні ───────────────────────────────────────────────
    kitchen_m = re.search(r'Кухня:\s*([\d,\.]+)\s*м²', raw)
    obj["kitchen"] = kitchen_m.group(1) if kitchen_m else ""

    # ── Поверх ────────────────────────────────────────────────────
    floor_m = re.search(r'Поверх:\s*(\d+)\s*з\s*(\d+)', raw)
    obj["floor"]       = f"{floor_m.group(1)}/{floor_m.group(2)}" if floor_m else "—"
    obj["floor_num"]   = int(floor_m.group(1)) if floor_m else 0
    obj["floors_total"]= int(floor_m.group(2)) if floor_m else 0

    # ── Кількість кімнат ──────────────────────────────────────────
    rooms_m = re.search(r'(\d+)-(?:к\b|кімнатн)', raw)
    obj["rooms"] = rooms_m.group(1) if rooms_m else "—"

    # ── Район ─────────────────────────────────────────────────────
    dist_m = re.search(
        r'(Личаківськ\w*|Сихівськ\w*|Галицьк\w*|Шевченківськ\w*|'
        r'Залізничн\w*|Франківськ\w*|Сихів)\s*(?:район)?',
        raw, re.IGNORECASE
    )
    obj["district"] = dist_m.group(1) if dist_m else "Львів"
    # Нормалізуємо назву
    dist_norm = {
        "личаківськ": "Личаківський",
        "сихівськ":   "Сихівський",
        "галицьк":    "Галицький",
        "шевченківськ":"Шевченківський",
        "залізничн":  "Залізничний",
        "франківськ": "Франківський",
        "сихів":      "Сихів",
    }
    for k, v in dist_norm.items():
        if obj["district"].lower().startswith(k):
            obj["district"] = v
            break

    # ── Вулиця ────────────────────────────────────────────────────
    street_m = re.search(r'вул\.\s+([^\,\.\n]+)', raw)
    obj["street"] = street_m.group(1).strip() if street_m else ""

    # ── Координати ────────────────────────────────────────────────
    coords = re.findall(r'(4[89]\.\d{5,})\s+(2[234]\.\d{5,})', raw)
    if coords:
        obj["lat"] = coords[0][0]
        obj["lng"] = coords[0][1]
    else:
        obj["lat"] = obj["lng"] = ""

    # ── Рік будівлі ───────────────────────────────────────────────
    year_m = re.search(r'(\d{4})\s+року', raw)
    obj["year"] = year_m.group(1) if year_m else "—"

    # ── Опалення ──────────────────────────────────────────────────
    heat_m = re.search(r'Опалення:\s*([^\n\(]+)', raw)
    obj["heating"] = heat_m.group(1).strip() if heat_m else ""

    # ── Агент: email, телефон, агентство ──────────────────────────
    email_m = re.search(r'[\w\.\-]+@[\w\.\-]+\.\w{2,}', raw)
    obj["agent_email"] = email_m.group(0) if email_m else ""

    phone_m = re.search(r'(0\d{9})', raw)
    obj["agent_phone"] = phone_m.group(1) if phone_m else ""

    agency_m = re.search(
        r'([A-ZА-ЯЇІЄҐ][a-zA-Zа-яїієґ\s]{2,}'
        r'(?:Capital|Estate|Agency|Realty|Нерухомість|Груп|Group))',
        raw
    )
    obj["agency"] = agency_m.group(1).strip() if agency_m else ""

    # ── Дати ──────────────────────────────────────────────────────
    dates = re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', raw)
    obj["created_at"] = dates[0][:10] if len(dates) > 0 else ""
    obj["updated_at"] = dates[1][:10] if len(dates) > 1 else ""

    # ── Заголовок ─────────────────────────────────────────────────
    title_m = re.search(
        r'(?:Продаж|Оренда)\s+(.+?)(?=\s{2,}|\s+Пропонується|\s+Здається)',
        raw
    )
    if title_m:
        obj["title"] = title_m.group(1).strip()
    else:
        rooms_str = f"{obj['rooms']}-кімн. " if obj["rooms"] != "—" else ""
        obj["title"] = (
            f"{obj['deal_type']} {rooms_str}квартири, "
            f"{obj['district']}, {obj['street']}"
        ).strip(", ")

    # ── Опис (перший абзац) ───────────────────────────────────────
    desc_m = re.search(
        r'(Пропонується.+?)(?=Локація:|Площа:|Поверх:|Переваги:)',
        raw, re.DOTALL
    )
    obj["description"] = desc_m.group(1).strip() if desc_m else ""

    # ── Переваги (короткий список) ────────────────────────────────
    adv_m = re.search(r'Переваги:(.*?)(?=Поруч|Ідеально|Телефонуйте|$)', raw, re.DOTALL)
    if adv_m:
        lines = [l.strip().lstrip("–-•").strip()
                 for l in adv_m.group(1).split("\n")
                 if l.strip().lstrip("–-• ")]
        obj["advantages"] = lines[:6]  # перші 6 переваг
    else:
        obj["advantages"] = []

    # ── Тип нерухомості ───────────────────────────────────────────
    raw_lower = raw.lower()
    if "будинок" in raw_lower or "будинку" in raw_lower:
        obj["property_type"] = "house"
    elif "ділянк" in raw_lower:
        obj["property_type"] = "land"
    else:
        obj["property_type"] = "apartment"

    # ── Тип для фільтру пошуку ────────────────────────────────────
    if "Оренда" in obj["deal_type"]:
        obj["search_type"] = "rent_apt"
    elif obj["property_type"] == "house":
        obj["search_type"] = "buy_house"
    elif obj["property_type"] == "land":
        obj["search_type"] = "land"
    else:
        obj["search_type"] = "buy_apt"

    # ── Локація (місто Львів або передмістя) ──────────────────────
    # Якщо координати є — перевіряємо відстань від центру Львова
    # Центр Львова: 49.8397, 24.0297
    obj["location"] = "lviv"  # default
    if obj.get("lat") and obj.get("lng"):
        try:
            lat = float(obj["lat"])
            lng = float(obj["lng"])
            # Проста формула відстані в км (Haversine спрощена)
            dlat = abs(lat - 49.8397) * 111.0
            dlng = abs(lng - 24.0297) * 111.0 * 0.63
            dist_km = (dlat**2 + dlng**2) ** 0.5
            obj["dist_km"] = round(dist_km, 1)
            if dist_km <= 5:
                obj["location"] = "lviv"
            elif dist_km <= 20:
                obj["location"] = "suburbs"  # передмістя до 20 км
            else:
                obj["location"] = "region"   # Львівська область
        except (ValueError, TypeError):
            obj["dist_km"] = 0
    else:
        obj["dist_km"] = 0
        # Визначаємо по тексту якщо нема координат
        raw_lower = raw.lower()
        suburbs = ["брюховичі","винники","пустомити","рудне","сокільники",
                   "малехів","дубляни","зимна вода","щирець","байківці",
                   "лисиничі","сокільники","підберізці","давидів"]
        for sub in suburbs:
            if sub in raw_lower:
                obj["location"] = "suburbs"
                break

    return obj


def format_card(obj: dict, short: bool = True) -> str:
    """Форматує картку об'єкту для Telegram."""
    emoji = {"rent_apt":"🏠","buy_apt":"🔑","buy_house":"🏡","land":"🌿"}
    e = emoji.get(obj.get("search_type","buy_apt"), "🏠")

    price = obj.get("price_usd", 0)
    currency = "₴/міс" if obj.get("search_type") == "rent_apt" else "$"
    price_str = f"{price:,} {currency}".replace(",", " ") if price else "Договірна"

    lines = [
        f"{e} <b>{obj.get('title','Об\'єкт')}</b>",
        "",
        f"💰 <b>{price_str}</b>",
    ]

    # Основні характеристики
    chars = []
    if obj.get("area") and obj["area"] != "—":
        chars.append(f"📐 {obj['area']} м²")
    if obj.get("rooms") and obj["rooms"] != "—":
        chars.append(f"🛏 {obj['rooms']} кімн.")
    if obj.get("floor") and obj["floor"] != "—":
        chars.append(f"🏢 {obj['floor']} пов.")
    # Локація: для передмістя показуємо відстань від Львова
    if obj.get("location") == "suburbs" and obj.get("dist_km",0) > 0:
        chars.append(f"🌳 {obj.get('district','Передмістя')} · {obj['dist_km']} км від Львова")
    elif obj.get("district"):
        chars.append(f"📍 {obj['district']}")
    if obj.get("year") and obj["year"] != "—":
        chars.append(f"🏗 {obj['year']} р.")
    if obj.get("heating"):
        chars.append(f"🔥 {obj['heating'][:25]}")

    lines.extend(chars)

    if not short:
        if obj.get("description"):
            lines += ["", f"📝 {obj['description'][:400]}"]
        if obj.get("advantages"):
            lines += ["", "✅ <b>Переваги:</b>"]
            for adv in obj["advantages"][:4]:
                lines.append(f"  — {adv}")
        if obj.get("street"):
            lines += ["", f"🗺 {obj.get('street','')}"]

    lines += ["", f"🆔 <code>#{obj.get('code','—')}</code>"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  БАЗА ОБ'ЄКТІВ
# ══════════════════════════════════════════════════════════════════
OBJECTS_DB: list[dict] = []

def load_objects(filepath: str = "objects.txt"):
    global OBJECTS_DB
    OBJECTS_DB = []

    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} не знайдено — база порожня")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    ok = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Приймаємо тільки активні рядки
        if not line.startswith("active"):
            continue
        obj = parse_crm_line(line)
        if obj:
            OBJECTS_DB.append(obj)
            ok += 1

    print(f"✅ Завантажено {ok} об'єктів з {filepath}")


def filter_objects(
    search_type: str = "all",
    district: str = "all",
    rooms: str = "all",
    price_from: int = 0,
    price_to: int = 0,
    location: str = "lviv",   # lviv / suburbs / all
) -> list[dict]:
    res = []
    for o in OBJECTS_DB:
        if o.get("status") != "active":
            continue
        if search_type != "all" and o.get("search_type") != search_type:
            continue

        # ── Фільтр по локації ──────────────────────────────────────
        if location == "lviv":
            # Тільки в межах міста (до 5 км від центру або район вказаний)
            if o.get("location") == "region":
                continue
            if o.get("location") == "suburbs" and not o.get("district"):
                continue
        elif location == "suburbs":
            # Передмістя 5–20 км від центру
            if o.get("location") not in ("suburbs",):
                continue
        # location == "all" — без фільтру

        # ── Фільтр по району (тільки для Львова) ──────────────────
        if district != "all" and location == "lviv":
            if district.lower() not in o.get("district", "").lower():
                continue

        if rooms not in ("all", "—"):
            if rooms == "4+":
                try:
                    if int(o.get("rooms", 0)) < 4:
                        continue
                except (ValueError, TypeError):
                    continue
            elif o.get("rooms") != rooms:
                continue

        price = o.get("price_usd", 0)
        if price_from > 0 and price < price_from:
            continue
        if price_to > 0 and price > price_to:
            continue
        res.append(o)
    return res


# ══════════════════════════════════════════════════════════════════
#  FSM СТАНИ
# ══════════════════════════════════════════════════════════════════
class SearchState(StatesGroup):
    choosing_rooms      = State()
    choosing_district   = State()
    entering_price_from = State()
    entering_price_to   = State()

class SubscribeState(StatesGroup):
    choosing_type       = State()
    choosing_district   = State()
    entering_price_from = State()
    entering_price_to   = State()
    choosing_frequency  = State()

class SellState(StatesGroup):
    entering_address = State()
    entering_details = State()
    entering_price   = State()
    entering_phone   = State()

class ContactState(StatesGroup):
    entering_name    = State()
    entering_phone   = State()
    entering_comment = State()

class AddObjectState(StatesGroup):
    waiting_data = State()


# ══════════════════════════════════════════════════════════════════
#  ПІДПИСНИКИ
# ══════════════════════════════════════════════════════════════════
subscriptions: dict[int, dict] = {}


# ══════════════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════════════
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Оренда квартири",            callback_data="menu:rent_apt")
    kb.button(text="📋 Здати квартиру",              callback_data="menu:give_apt")
    kb.button(text="🔑 Купити квартиру",             callback_data="menu:buy_apt")
    kb.button(text="🏡 Купити будинок",              callback_data="menu:buy_house")
    kb.button(text="💰 Продаж квартири",             callback_data="menu:sell_apt")
    kb.button(text="🏘 Продаж будинку",              callback_data="menu:sell_house")
    kb.button(text="🌿 Земельні ділянки",            callback_data="menu:land")
    kb.button(text="🔔 Сповіщення про нові об'єкти", callback_data="menu:subscribe")
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()

def rooms_kb():
    kb = InlineKeyboardBuilder()
    for r in ["Студія", "1", "2", "3", "4+"]:
        kb.button(text=r, callback_data=f"rooms:{r}")
    kb.button(text="Будь-яка", callback_data="rooms:all")
    kb.button(text="◀ Назад",  callback_data="back:main")
    kb.adjust(5, 1, 1)
    return kb.as_markup()

def district_kb(suburbs: bool = False):
    kb = InlineKeyboardBuilder()
    if not suburbs:
        districts = ["Галицький","Сихів","Шевченківський",
                     "Залізничний","Франківський","Личаківський"]
        for d in districts:
            kb.button(text=d, callback_data=f"district:{d}")
        kb.button(text="🏙 Всі райони Львова",     callback_data="district:all")
        kb.button(text="🌳 Передмістя (+20 км)",    callback_data="location:suburbs")
    else:
        suburbs_list = ["Брюховичі","Винники","Пустомити","Рудне",
                        "Сокільники","Малехів","Зимна Вода","Давидів"]
        for s in suburbs_list:
            kb.button(text=s, callback_data=f"suburb:{s}")
        kb.button(text="🌍 Всі передмістя",         callback_data="suburb:all")
        kb.button(text="🏙 Повернутись до Львова",   callback_data="district:all")
    kb.button(text="◀ Назад", callback_data="back:main")
    kb.adjust(2, 2, 2, 1, 1, 1)
    return kb.as_markup()

def freq_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚡ Одразу",         callback_data="freq:instant")
    kb.button(text="📅 Раз на день",    callback_data="freq:daily")
    kb.button(text="📆 Раз на тиждень", callback_data="freq:weekly")
    kb.adjust(1)
    return kb.as_markup()

def obj_kb(code: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записатись на перегляд", callback_data=f"view:{code}")
    kb.button(text="👤 Зв'язатись з ріелтором",  callback_data="action:contact")
    kb.button(text="🔔 Сповіщати про схожі",      callback_data="menu:subscribe")
    kb.button(text="◀ Головне меню",              callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()

def back_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Головне меню",            callback_data="back:main")
    kb.button(text="👤 Зв'язатись з ріелтором",  callback_data="action:contact")
    kb.adjust(1)
    return kb.as_markup()


# ══════════════════════════════════════════════════════════════════
#  ВІДПРАВКА ОБ'ЄКТУ З ФОТО
# ══════════════════════════════════════════════════════════════════
async def send_obj(msg: Message, obj: dict, short: bool = True):
    text   = format_card(obj, short=short)
    code   = str(obj.get("code", ""))
    photos = obj.get("photos", [])
    try:
        if photos:
            await msg.answer_photo(
                photo=photos[0], caption=text,
                parse_mode="HTML", reply_markup=obj_kb(code)
            )
        else:
            await msg.answer(text, parse_mode="HTML", reply_markup=obj_kb(code))
    except TelegramBadRequest:
        await msg.answer(text, parse_mode="HTML", reply_markup=obj_kb(code))


async def send_gallery(msg: Message, obj: dict):
    """Повна картка + галерея всіх фото."""
    photos = obj.get("photos", [])
    if len(photos) <= 1:
        await send_obj(msg, obj, short=False)
        return

    # Надсилаємо альбом (до 10 фото)
    media = []
    for i, url in enumerate(photos[:10]):
        caption = format_card(obj, short=False) if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=caption, parse_mode="HTML"))
    try:
        await msg.answer_media_group(media=media)
        await msg.answer(
            f"📸 {len(photos)} фото  |  🆔 #{obj.get('code','')}",
            reply_markup=obj_kb(str(obj.get("code","")))
        )
    except TelegramBadRequest:
        await send_obj(msg, obj, short=False)


# ══════════════════════════════════════════════════════════════════
#  КОМАНДИ
# ══════════════════════════════════════════════════════════════════
WELCOME = (
    "👋 Вітаю! Я AI-асистент агентства нерухомості у <b>Львові</b>.\n\n"
    "🏠 Актуальна база квартир, будинків та ділянок.\n"
    "🔔 Підпишіться — і я першим напишу про новий об'єкт!\n\n"
    "Оберіть що вас цікавить:"
)

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("✅ Скасовано.", reply_markup=main_menu_kb())

@dp.message(Command("mysub"))
async def cmd_mysub(msg: Message):
    uid = msg.from_user.id
    if uid not in subscriptions:
        await msg.answer("🔕 Підписок немає.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text="🔔 Налаштувати", callback_data="menu:subscribe"
            )]]
        ))
        return
    s = subscriptions[uid]
    fl = {"instant":"одразу","daily":"раз на день","weekly":"раз на тиждень"}
    await msg.answer(
        f"🔔 <b>Ваша підписка:</b>\n\n"
        f"🏠 {s.get('type','—')}\n📍 {s.get('district','всі')}\n"
        f"💰 {s.get('price_from',0):,}–{s.get('price_to',0):,} $\n"
        f"⏰ {fl.get(s.get('frequency','instant'))}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Змінити", callback_data="menu:subscribe"),
             InlineKeyboardButton(text="❌ Вимкнути", callback_data="action:unsub")],
            [InlineKeyboardButton(text="◀ Меню",    callback_data="back:main")],
        ])
    )

# ── /add_object — тільки для менеджера ─────────────────────────
@dp.message(Command("add_object"))
async def cmd_add(msg: Message, state: FSMContext):
    if str(msg.from_user.id) != str(AGENT_CHAT_ID):
        return
    await state.set_state(AddObjectState.waiting_data)
    await msg.answer(
        "📋 <b>Додати об'єкт з CRM</b>\n\n"
        "Вставте рядок експорту з RealtSoft (один рядок = один об'єкт):",
        parse_mode="HTML"
    )

@dp.message(AddObjectState.waiting_data)
async def receive_crm(msg: Message, state: FSMContext):
    obj = parse_crm_line(msg.text)
    if not obj:
        await msg.answer("⚠️ Не вдалось розпізнати. Перевірте формат рядка.")
        return

    # Оновити або додати
    codes = [o.get("code") for o in OBJECTS_DB]
    if obj["code"] in codes:
        OBJECTS_DB[:] = [obj if o["code"] == obj["code"] else o for o in OBJECTS_DB]
        action = "оновлено ♻️"
    else:
        OBJECTS_DB.append(obj)
        action = "додано ✅"

    await state.clear()
    await msg.answer(
        f"Об'єкт <b>{action}</b>\n\n"
        f"🆔 #{obj['code']}  📍 {obj['district']}  "
        f"💰 {obj.get('price_usd',0):,} $  📸 {len(obj.get('photos',[]))} фото\n\n"
        f"Надіслати сповіщення підписникам?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Так, розіслати всім", callback_data=f"notify:{obj['code']}"),
             InlineKeyboardButton(text="⏭ Пропустити",          callback_data="back:main")],
        ])
    )

@dp.callback_query(F.data.startswith("notify:"))
async def do_notify(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    obj  = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    if not obj:
        await cb.answer("Об'єкт не знайдено"); return
    await cb.answer("Розсилаю...")
    n = await broadcast_new_object(obj)
    await cb.message.answer(f"✅ Сповіщення надіслано <b>{n}</b> підписникам!", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ — callbacks
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "back:main")
async def back_main(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("menu:"))
async def handle_menu(cb: CallbackQuery, state: FSMContext):
    s = cb.data.split(":")[1]
    await cb.answer()

    if s in ("rent_apt", "buy_apt"):
        cur = "₴" if s == "rent_apt" else "$"
        lbl = "🏠 Оренда" if s == "rent_apt" else "🔑 Купівля квартири"
        await state.update_data(search_type=s, currency=cur)
        await cb.message.edit_text(f"{lbl}\n\nОберіть кімнати:", reply_markup=rooms_kb())
        await state.set_state(SearchState.choosing_rooms)

    elif s == "buy_house":
        await state.update_data(search_type="buy_house", currency="$")
        await cb.message.edit_text("🏡 Купівля будинку\n\nОберіть район:", reply_markup=district_kb())
        await state.set_state(SearchState.choosing_district)

    elif s in ("give_apt","sell_apt","sell_house"):
        prompts = {
            "give_apt":   "📋 <b>Здати квартиру</b>\n\nБезкоштовно на 40+ майданчиках!\n\n📍 Адреса:",
            "sell_apt":   "💰 <b>Продаж квартири</b>\n\n✅ Оцінка · Фото · 40+ майданчиків · Юридичний супровід\n\n📍 Адреса:",
            "sell_house": "🏘 <b>Продаж будинку</b>\n\n✅ Оцінка · Фотосесія · Юридичний супровід\n\n📍 Адреса:",
        }
        await state.update_data(sell_type=s if s != "give_apt" else None)
        await state.set_state(SellState.entering_address)
        await cb.message.edit_text(
            prompts[s], parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
            ]])
        )

    elif s == "land":
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Купити ділянку",  callback_data="land:buy")
        kb.button(text="💰 Продати ділянку", callback_data="land:sell")
        kb.button(text="◀ Назад",            callback_data="back:main")
        kb.adjust(2, 1)
        await cb.message.edit_text(
            "🌿 <b>Земельні ділянки</b>\n\nЩо вас цікавить?",
            parse_mode="HTML", reply_markup=kb.as_markup()
        )

    elif s == "subscribe":
        await open_subscribe(cb.message, state, edit=True)


# ══════════════════════════════════════════════════════════════════
#  ПОШУК — FSM
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(SearchState.choosing_rooms, F.data.startswith("rooms:"))
async def pick_rooms(cb: CallbackQuery, state: FSMContext):
    r = cb.data.split(":")[1]
    await state.update_data(rooms=r)
    await cb.answer()
    lbl = "будь-яка" if r == "all" else r
    await cb.message.edit_text(
        f"✅ Кімнат: <b>{lbl}</b>\n\nОберіть район:",
        parse_mode="HTML", reply_markup=district_kb()
    )
    await state.set_state(SearchState.choosing_district)

@dp.callback_query(SearchState.choosing_district, F.data.startswith("district:"))
@dp.callback_query(F.data.startswith("district:"))
async def pick_district(cb: CallbackQuery, state: FSMContext):
    d = cb.data.split(":")[1]
    await state.update_data(district=d, location="lviv", suburb="all")
    data = await state.get_data()
    cur  = data.get("currency", "$")
    hint = ". Мінімум оренди 10 000 ₴" if cur == "₴" else ""
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Район: <b>{'Всі райони Львова' if d=='all' else d}</b>\n\n"
        f"💰 Мінімальна ціна ({cur})\n<i>0 — без обмежень{hint}</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )
    await state.set_state(SearchState.entering_price_from)

# ── Передмістя — перехід до списку населених пунктів ──────────────
@dp.callback_query(SearchState.choosing_district, F.data == "location:suburbs")
@dp.callback_query(F.data == "location:suburbs")
async def pick_suburbs_menu(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "🌳 <b>Передмістя Львова (+20 км)</b>\n\n"
        "Оберіть населений пункт або шукайте по всіх:",
        parse_mode="HTML",
        reply_markup=district_kb(suburbs=True)
    )
    await state.set_state(SearchState.choosing_district)

@dp.callback_query(SearchState.choosing_district, F.data.startswith("suburb:"))
@dp.callback_query(F.data.startswith("suburb:"))
async def pick_suburb(cb: CallbackQuery, state: FSMContext):
    suburb = cb.data.split(":")[1]
    await state.update_data(location="suburbs", suburb=suburb, district="all")
    data = await state.get_data()
    cur  = data.get("currency", "$")
    lbl  = "Всі передмістя" if suburb == "all" else suburb
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Локація: <b>🌳 {lbl}</b>\n\n"
        f"💰 Мінімальна ціна ({cur})\n<i>0 — без обмежень</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )
    await state.set_state(SearchState.entering_price_from)

@dp.message(SearchState.entering_price_from)
async def spf(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Число, наприклад: 50000"); return
    data = await state.get_data()
    pf = int(t)
    if data.get("currency") == "₴" and 0 < pf < 10000:
        await msg.answer("⚠️ Мінімум оренди 10 000 ₴:"); return
    await state.update_data(price_from=pf)
    await msg.answer(
        f"✅ Від: <b>{pf:,}</b>\n\n💰 Максимальна ціна\n<i>0 — без обмежень</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchState.entering_price_to)

@dp.message(SearchState.entering_price_to)
async def spt(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Число, наприклад: 150000"); return
    await state.update_data(price_to=int(t))
    data = await state.get_data()
    await do_search(msg, data)
    await state.clear()

async def do_search(msg: Message, d: dict):
    stype    = d.get("search_type","buy_apt")
    rooms    = d.get("rooms","all")
    dist     = d.get("district","all")
    pf       = d.get("price_from",0)
    pt       = d.get("price_to",0)
    cur      = d.get("currency","$")
    location = d.get("location","lviv")
    suburb   = d.get("suburb","all")
    lbl      = {"rent_apt":"Оренда квартири","buy_apt":"Купівля квартири",
                "buy_house":"Купівля будинку","land":"Ділянки"}
    pr       = f"{pf:,}–{pt:,} {cur}" if pt else f"від {pf:,} {cur}"

    # Локаційний рядок для відображення
    if location == "suburbs":
        loc_label = f"🌳 {'Всі передмістя' if suburb=='all' else suburb} (+20 км)"
    else:
        loc_label = f"📍 {'Всі райони Львова' if dist=='all' else dist}"

    await msg.answer(
        f"🔍 <b>{lbl.get(stype,stype)}</b>\n"
        f"🛏 {'будь-яка' if rooms=='all' else rooms} кімн.  "
        f"{loc_label}  💰 {pr}\n\n"
        f"⏳ <i>Шукаю в базі...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(1.0)

    results = filter_objects(
        search_type=stype, district=dist,
        rooms=rooms, price_from=pf, price_to=pt,
        location=location
    )
    # Якщо шукаємо конкретне передмістя — фільтруємо по назві
    if location == "suburbs" and suburb != "all":
        results = [o for o in results
                   if suburb.lower() in (o.get("street","") + o.get("district","") + o.get("raw","")).lower()]

    if not results:
        await msg.answer(
            "😔 <b>Нічого не знайдено за вашими параметрами.</b>\n\n"
            "Підпишіться — я напишу щойно з'явиться підходящий об'єкт!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Підписатись",    callback_data="menu:subscribe")],
                [InlineKeyboardButton(text="◀ Змінити параметри", callback_data="back:main")],
            ])
        )
        return

    await msg.answer(
        f"✅ Знайдено <b>{len(results)}</b> об'єктів — показую перші {min(3,len(results))}:",
        parse_mode="HTML"
    )
    for obj in results[:3]:
        await asyncio.sleep(0.3)
        await send_obj(msg, obj, short=True)

    if len(results) > 3:
        kb = InlineKeyboardBuilder()
        kb.button(text=f"📋 Всі {len(results)}", callback_data="action:show_all")
        kb.button(text="🔔 Сповіщати про нові",  callback_data="menu:subscribe")
        kb.button(text="👤 Ріелтор",             callback_data="action:contact")
        kb.adjust(1)
        await msg.answer(f"...ще {len(results)-3} об'єктів", reply_markup=kb.as_markup())


# ══════════════════════════════════════════════════════════════════
#  ПЕРЕГЛЯД + ГАЛЕРЕЯ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("view:"))
async def cb_view(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    await cb.answer()
    obj = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    if obj:
        await send_gallery(cb.message, obj)
    else:
        await cb.message.answer(
            f"📅 <b>Запис на перегляд #{code}</b>\n\n"
            f"📆 Завтра — 10:00 · 13:00 · 16:00\n"
            f"📱 Зателефонуйте: +38 067 123 45 67",
            parse_mode="HTML", reply_markup=back_kb()
        )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"👁 <b>ЗАПИТ НА ПЕРЕГЛЯД</b>\n"
            f"🆔 #{code}\n"
            f"👤 @{cb.from_user.username or '—'} · {cb.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  ЗЕМЕЛЬНІ ДІЛЯНКИ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "land:buy")
async def land_buy(cb: CallbackQuery, state: FSMContext):
    await state.update_data(search_type="land", currency="$")
    await cb.answer()
    await cb.message.edit_text("🌿 Купівля ділянки\n\nОберіть напрямок:", reply_markup=district_kb())
    await state.set_state(SearchState.choosing_district)

@dp.callback_query(F.data == "land:sell")
async def land_sell(cb: CallbackQuery, state: FSMContext):
    await state.update_data(sell_type="land_sell")
    await state.set_state(SellState.entering_address)
    await cb.answer()
    await cb.message.edit_text(
        "🌿 <b>Продаж ділянки</b>\n\n✅ Оцінка · Документи · Розміщення\n\n📍 Адреса/напрямок:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )


# ══════════════════════════════════════════════════════════════════
#  ПРОДАЖ / ЗДАЧА — FSM
# ══════════════════════════════════════════════════════════════════
@dp.message(SellState.entering_address)
async def sell_addr(msg: Message, state: FSMContext):
    await state.update_data(address=msg.text)
    data  = await state.get_data()
    stype = data.get("sell_type","")
    if stype == "land_sell":
        p = "📐 Площа та комунікації:\n<i>10 соток, газ + електрика</i>"
    elif stype == "sell_house":
        p = "📐 Площа, поверхів, ділянка:\n<i>180 м², 2 пов., 12 соток</i>"
    else:
        p = "📐 Площа, поверх, рік:\n<i>58 м², 7/12, 2022</i>"
    await msg.answer(p, parse_mode="HTML")
    await state.set_state(SellState.entering_details)

@dp.message(SellState.entering_details)
async def sell_det(msg: Message, state: FSMContext):
    await state.update_data(details=msg.text)
    await msg.answer("💰 Ціна:\n<i>Або «оцінка» — ріелтор оцінить безкоштовно</i>", parse_mode="HTML")
    await state.set_state(SellState.entering_price)

@dp.message(SellState.entering_price)
async def sell_pr(msg: Message, state: FSMContext):
    await state.update_data(price=msg.text)
    await msg.answer("📱 Ваш номер телефону:")
    await state.set_state(SellState.entering_phone)

@dp.message(SellState.entering_phone)
async def sell_ph(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    data  = await state.get_data()
    lbls  = {"sell_apt":"Продаж квартири","sell_house":"Продаж будинку",
             "land_sell":"Продаж ділянки"}
    label = lbls.get(data.get("sell_type",""), "Здача в оренду")
    await msg.answer(
        f"✅ <b>Заявку отримано!</b>\n\n"
        f"📋 {label}\n📍 {data.get('address','—')}\n"
        f"📐 {data.get('details','—')}\n💰 {data.get('price','—')}\n"
        f"📱 {data.get('phone','—')}\n\n"
        f"⏰ Ріелтор зателефонує протягом <b>15 хвилин</b>!",
        parse_mode="HTML", reply_markup=back_kb()
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"🔔 <b>НОВА ЗАЯВКА — {label.upper()}</b>\n\n"
            f"📍 {data.get('address','—')}\n📐 {data.get('details','—')}\n"
            f"💰 {data.get('price','—')}\n📱 {data.get('phone','—')}\n"
            f"👤 @{msg.from_user.username or '—'} · {msg.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()


# ══════════════════════════════════════════════════════════════════
#  КОНТАКТ З РІЕЛТОРОМ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "action:contact")
async def cb_contact(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(ContactState.entering_name)
    await cb.message.answer("👤 Ваше ім'я:", reply_markup=ReplyKeyboardRemove())

@dp.message(ContactState.entering_name)
async def cn(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("📱 Номер телефону:")
    await state.set_state(ContactState.entering_phone)

@dp.message(ContactState.entering_phone)
async def cp(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    await msg.answer("💬 Що вас цікавить?\n<i>Наприклад: 2-кімн. у Сихові до $70k</i>",
                     parse_mode="HTML")
    await state.set_state(ContactState.entering_comment)

@dp.message(ContactState.entering_comment)
async def cc(msg: Message, state: FSMContext):
    await state.update_data(comment=msg.text)
    data = await state.get_data()
    await msg.answer(
        f"✅ Дякую, <b>{data.get('name','')}</b>!\n\n"
        f"Ріелтор зателефонує на <b>{data.get('phone','')}</b> за 15 хвилин.\n"
        f"📞 Або самі: +38 067 123 45 67",
        parse_mode="HTML", reply_markup=back_kb()
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"📞 <b>ЗАПИТ НА ЗВ'ЯЗОК</b>\n"
            f"👤 {data.get('name','—')}\n📱 {data.get('phone','—')}\n"
            f"💬 {data.get('comment','—')}\n"
            f"🔗 @{msg.from_user.username or '—'} · {msg.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()


# ══════════════════════════════════════════════════════════════════
#  ПІДПИСКА
# ══════════════════════════════════════════════════════════════════
async def open_subscribe(msg, state: FSMContext, edit: bool = False):
    kb = InlineKeyboardBuilder()
    for lbl, dat in [
        ("🏠 Оренда квартири","sub_type:rent_apt"),
        ("🔑 Купівля квартири","sub_type:buy_apt"),
        ("🏡 Купівля будинку", "sub_type:buy_house"),
        ("🌿 Земельна ділянка","sub_type:land"),
        ("📦 Всі типи",        "sub_type:all"),
    ]:
        kb.button(text=lbl, callback_data=dat)
    kb.button(text="◀ Назад", callback_data="back:main")
    kb.adjust(1)
    t = "🔔 <b>Сповіщення</b>\n\nОберіть тип нерухомості:"
    if edit:
        await msg.edit_text(t, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await msg.answer(t, parse_mode="HTML", reply_markup=kb.as_markup())
    await state.set_state(SubscribeState.choosing_type)

@dp.callback_query(SubscribeState.choosing_type, F.data.startswith("sub_type:"))
async def sub_type(cb: CallbackQuery, state: FSMContext):
    st = cb.data.split(":")[1]
    cur = "₴" if st == "rent_apt" else "$"
    await state.update_data(sub_type=st, currency=cur)
    await cb.answer()
    await cb.message.edit_text("📍 Оберіть район:", reply_markup=district_kb())
    await state.set_state(SubscribeState.choosing_district)

@dp.callback_query(SubscribeState.choosing_district, F.data.startswith("district:"))
async def sub_district(cb: CallbackQuery, state: FSMContext):
    d = cb.data.split(":")[1]
    await state.update_data(sub_district=d, sub_location="lviv")
    data = await state.get_data()
    cur  = data.get("currency","$")
    await cb.answer()
    hint = ". Мінімум 10 000 ₴" if cur == "₴" else ""
    await cb.message.edit_text(
        f"✅ Район: <b>{'Всі райони Львова' if d=='all' else d}</b>\n\n"
        f"💰 Мінімальний бюджет ({cur})\n<i>0 — без обмежень{hint}</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )
    await state.set_state(SubscribeState.entering_price_from)

@dp.callback_query(SubscribeState.choosing_district, F.data == "location:suburbs")
async def sub_suburbs(cb: CallbackQuery, state: FSMContext):
    await state.update_data(sub_district="all", sub_location="suburbs")
    data = await state.get_data()
    cur  = data.get("currency","$")
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Локація: <b>🌳 Передмістя Львова (+20 км)</b>\n\n"
        f"💰 Мінімальний бюджет ({cur})\n<i>0 — без обмежень</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )
    await state.set_state(SubscribeState.entering_price_from)

@dp.message(SubscribeState.entering_price_from)
async def sub_pf(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Число:"); return
    data = await state.get_data()
    pf = int(t)
    if data.get("currency") == "₴" and 0 < pf < 10000:
        await msg.answer("⚠️ Мінімум 10 000 ₴:"); return
    await state.update_data(sub_price_from=pf)
    await msg.answer(f"✅ Від {pf:,}\n\n💰 Максимальний бюджет\n<i>0 — без обмежень</i>",
                     parse_mode="HTML")
    await state.set_state(SubscribeState.entering_price_to)

@dp.message(SubscribeState.entering_price_to)
async def sub_pt(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Число:"); return
    await state.update_data(sub_price_to=int(t))
    await msg.answer("⏰ Як часто надсилати?", reply_markup=freq_kb())
    await state.set_state(SubscribeState.choosing_frequency)

@dp.callback_query(SubscribeState.choosing_frequency, F.data.startswith("freq:"))
async def sub_freq(cb: CallbackQuery, state: FSMContext):
    freq = cb.data.split(":")[1]
    data = await state.get_data()
    uid  = cb.from_user.id
    subscriptions[uid] = {
        "type":       data.get("sub_type","all"),
        "district":   data.get("sub_district","all"),
        "price_from": data.get("sub_price_from",0),
        "price_to":   data.get("sub_price_to",0),
        "currency":   data.get("currency","$"),
        "frequency":  freq,
    }
    fl = {"instant":"одразу ✅","daily":"раз на день 📅","weekly":"раз на тиждень 📆"}
    tl = {"rent_apt":"Оренда кв.","buy_apt":"Купівля кв.","buy_house":"Купівля будинку",
          "land":"Ділянка","all":"Всі типи"}
    cur = data.get("currency","$")
    pt  = data.get("sub_price_to",0)
    pr  = f"{data.get('sub_price_from',0):,}–{pt:,} {cur}" if pt else f"від {data.get('sub_price_from',0):,} {cur}"
    await cb.answer()
    await cb.message.edit_text(
        f"🔔 <b>Сповіщення увімкнено!</b>\n\n"
        f"🏠 {tl.get(data.get('sub_type','all'))}\n"
        f"📍 {'Всі' if data.get('sub_district')=='all' else data.get('sub_district')}\n"
        f"💰 {pr}\n⏰ {fl.get(freq)}\n\n"
        f"Я перший надішлю вам нові об'єкти!\n<i>/mysub — керування підпискою</i>",
        parse_mode="HTML", reply_markup=back_kb()
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"🔔 <b>НОВИЙ ПІДПИСНИК</b>\n"
            f"👤 @{cb.from_user.username or '—'} · {uid}\n"
            f"🏠 {tl.get(data.get('sub_type','all'))} · {data.get('sub_district')}\n"
            f"💰 {pr} · {fl.get(freq)}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()

@dp.callback_query(F.data == "action:unsub")
async def do_unsub(cb: CallbackQuery):
    subscriptions.pop(cb.from_user.id, None)
    await cb.answer("Вимкнено")
    await cb.message.edit_text(
        "🔕 Сповіщення вимкнено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Увімкнути", callback_data="menu:subscribe"),
             InlineKeyboardButton(text="🏠 Меню",      callback_data="back:main")],
        ])
    )


# ══════════════════════════════════════════════════════════════════
#  РОЗСИЛКА — НОВИЙ ОБ'ЄКТ
# ══════════════════════════════════════════════════════════════════
async def broadcast_new_object(obj: dict) -> int:
    """Надсилає сповіщення всім підписникам що відповідають параметрам."""
    sent = 0
    for uid, sub in subscriptions.items():
        if sub["type"] != "all" and sub["type"] != obj.get("search_type"):
            continue
        if sub["district"] != "all":
            if sub["district"].lower() not in obj.get("district","").lower():
                continue
        price = obj.get("price_usd", 0)
        if sub["price_from"] > 0 and price < sub["price_from"]:
            continue
        if sub["price_to"] > 0 and price > sub["price_to"]:
            continue

        text = "🔔 <b>Новий об'єкт за вашим запитом!</b>\n\n" + format_card(obj, short=True)
        kb   = InlineKeyboardBuilder()
        kb.button(text="📅 Записатись на перегляд", callback_data=f"view:{obj.get('code','')}")
        kb.button(text="👤 Зв'язатись з ріелтором",  callback_data="action:contact")
        kb.button(text="⚙️ Керувати підпискою",      callback_data="action:unsub")
        kb.adjust(1)

        try:
            photos = obj.get("photos", [])
            if photos:
                await bot.send_photo(uid, photo=photos[0], caption=text,
                                     parse_mode="HTML", reply_markup=kb.as_markup())
            else:
                await bot.send_message(uid, text, parse_mode="HTML",
                                       reply_markup=kb.as_markup())
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    return sent


# ══════════════════════════════════════════════════════════════════
#  CATCH-ALL
# ══════════════════════════════════════════════════════════════════
@dp.message()
async def catch_all(msg: Message, state: FSMContext):
    if await state.get_state():
        return
    await msg.answer("🏠 Оберіть дію:", reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════
async def main():
    load_objects("objects.txt")
    print(f"🤖 Бот запущено. Об'єктів: {len(OBJECTS_DB)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
