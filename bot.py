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
AGENT_PHONE   = os.getenv("AGENT_PHONE", "+38 067 123 45 67")
AGENCY_NAME   = os.getenv("AGENCY_NAME", "Empire Capital Lviv")

bot     = Bot(token=TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ══════════════════════════════════════════════════════════════════
#  ПАРСЕР REALTSOFT CRM
# ══════════════════════════════════════════════════════════════════
def parse_crm_line(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw or not raw.startswith("active"):
        return None
    parts = raw.split()
    if len(parts) < 5:
        return None

    obj = {"status": parts[0], "num": parts[1], "code": parts[2], "deal_type": parts[3], "raw": raw}

    obj["photos"] = re.findall(r'https://[^\s]+/estate-images/watermark/[^\s]+\.jpg', raw)
    agent_photos  = re.findall(r'https://[^\s]+/user/[^\s]+\.jpg', raw)
    obj["agent_photo"] = agent_photos[0] if agent_photos else ""

    price_m = re.search(r'Ціна:\s*([\d\s]+?)\s*\x24', raw)
    if price_m:
        obj["price_usd"] = int(price_m.group(1).replace(" ", ""))
    else:
        pm2 = re.search(r'(\d[\d ]+?)\s*\x24', raw)
        obj["price_usd"] = int(pm2.group(1).replace(" ", "")) if pm2 else 0

    code_m = re.search(r'Код\s+#(\d+)', raw)
    if code_m:
        obj["code"] = code_m.group(1)

    area_m = re.search(r'Площа:\s*([\d,\.]+)\s*м²', raw)
    obj["area"] = area_m.group(1).replace(",", ".") if area_m else "—"

    kitchen_m = re.search(r'Кухня:\s*([\d,\.]+)\s*м²', raw)
    obj["kitchen"] = kitchen_m.group(1) if kitchen_m else ""

    floor_m = re.search(r'Поверх:\s*(\d+)\s*з\s*(\d+)', raw)
    obj["floor"]        = f"{floor_m.group(1)}/{floor_m.group(2)}" if floor_m else "—"
    obj["floor_num"]    = int(floor_m.group(1)) if floor_m else 0
    obj["floors_total"] = int(floor_m.group(2)) if floor_m else 0

    rooms_m = re.search(r'(\d+)-(?:к\b|кімнатн)', raw)
    obj["rooms"] = rooms_m.group(1) if rooms_m else "—"

    dist_m = re.search(
        r'(Личаківськ\w*|Сихівськ\w*|Галицьк\w*|Шевченківськ\w*|Залізничн\w*|Франківськ\w*|Сихів)\s*(?:район)?',
        raw, re.IGNORECASE
    )
    raw_district = dist_m.group(1) if dist_m else "Львів"
    norm = {"личаківськ":"Личаківський","сихівськ":"Сихівський","галицьк":"Галицький",
            "шевченківськ":"Шевченківський","залізничн":"Залізничний","франківськ":"Франківський","сихів":"Сихів"}
    obj["district"] = next((v for k,v in norm.items() if raw_district.lower().startswith(k)), raw_district)

    street_m = re.search(r'вул\.\s+([^\,\.\n]+)', raw)
    obj["street"] = street_m.group(1).strip() if street_m else ""

    coords = re.findall(r'(4[89]\.\d{5,})\s+(2[234]\.\d{5,})', raw)
    obj["lat"] = coords[0][0] if coords else ""
    obj["lng"] = coords[0][1] if coords else ""

    year_m = re.search(r'(\d{4})\s+року', raw)
    obj["year"] = year_m.group(1) if year_m else "—"

    heat_m = re.search(r'Опалення:\s*([^\n\(]+)', raw)
    obj["heating"] = heat_m.group(1).strip() if heat_m else ""

    email_m = re.search(r'[\w\.\-]+@[\w\.\-]+\.\w{2,}', raw)
    obj["agent_email"] = email_m.group(0) if email_m else ""

    phone_m = re.search(r'(0\d{9})', raw)
    obj["agent_phone"] = phone_m.group(1) if phone_m else ""

    agency_m = re.search(r'([A-ZА-ЯЇІЄҐ][a-zA-Zа-яїієґ\s]{2,}(?:Capital|Estate|Agency|Realty|Нерухомість|Груп|Group))', raw)
    obj["agency"] = agency_m.group(1).strip() if agency_m else ""

    dates = re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', raw)
    obj["created_at"] = dates[0][:10] if dates else ""
    obj["updated_at"] = dates[1][:10] if len(dates) > 1 else ""

    title_m = re.search(r'(?:Продаж|Оренда)\s+(.+?)(?=\s{2,}|\s+Пропонується|\s+Здається)', raw)
    obj["title"] = title_m.group(1).strip() if title_m else f"{obj['deal_type']} {obj['rooms']}-кімн., {obj['district']}"

    desc_m = re.search(r'(Пропонується.+?)(?=Локація:|Площа:|Поверх:|Переваги:)', raw, re.DOTALL)
    obj["description"] = desc_m.group(1).strip() if desc_m else ""

    adv_m = re.search(r'Переваги:(.*?)(?=Поруч|Ідеально|Телефонуйте|$)', raw, re.DOTALL)
    if adv_m:
        obj["advantages"] = [l.strip().lstrip("–-•").strip() for l in adv_m.group(1).split("\n") if l.strip().lstrip("–-• ")][:6]
    else:
        obj["advantages"] = []

    raw_lower = raw.lower()
    obj["property_type"] = "house" if "будинок" in raw_lower or "будинку" in raw_lower else ("land" if "ділянк" in raw_lower else "apartment")

    if "Оренда" in obj["deal_type"]:
        obj["search_type"] = "rent_apt"
    elif obj["property_type"] == "house":
        obj["search_type"] = "buy_house"
    elif obj["property_type"] == "land":
        obj["search_type"] = "land"
    else:
        obj["search_type"] = "buy_apt"

    obj["location"] = "lviv"
    obj["dist_km"] = 0
    if obj.get("lat") and obj.get("lng"):
        try:
            lat, lng = float(obj["lat"]), float(obj["lng"])
            dlat = abs(lat - 49.8397) * 111.0
            dlng = abs(lng - 24.0297) * 111.0 * 0.63
            dist = round((dlat**2 + dlng**2)**0.5, 1)
            obj["dist_km"] = dist
            obj["location"] = "lviv" if dist <= 5 else ("suburbs" if dist <= 20 else "region")
        except (ValueError, TypeError):
            pass
    else:
        suburbs_kw = ["брюховичі","винники","пустомити","рудне","сокільники","малехів","зимна вода","давидів"]
        if any(s in raw_lower for s in suburbs_kw):
            obj["location"] = "suburbs"

    return obj


# ══════════════════════════════════════════════════════════════════
#  ДИЗАЙН — форматування повідомлень
#  Telegram підтримує: <b>жирний</b> <i>курсив</i> <code>моноширний</code>
#  Використовуємо unicode-символи для красивого дизайну
# ══════════════════════════════════════════════════════════════════

# Розділювачі та декор
DIV  = "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️"
DIV2 = "━━━━━━━━━━━━━━━━━━━━"
DOT  = "·"

def price_format(price: int, currency: str = "$") -> str:
    """Красиве форматування ціни."""
    if not price:
        return "💬 Ціна договірна"
    s = f"{price:,}".replace(",", " ")
    return f"{s} {currency}"

def format_card(obj: dict, short: bool = True) -> str:
    """Красива картка об'єкту."""
    deal = obj.get("deal_type", "Продаж")
    stype = obj.get("search_type", "buy_apt")
    currency = "₴/міс" if stype == "rent_apt" else "$"
    price = obj.get("price_usd", 0)

    # Заголовок — великий і помітний
    title = obj.get("title", "Об'єкт нерухомості")
    # Обрізаємо якщо занадто довгий
    if len(title) > 60:
        title = title[:57] + "..."

    lines = [
        f"{'🔑' if 'Купи' in deal or stype=='buy_apt' else '🏠' if stype=='rent_apt' else '🏡' if stype=='buy_house' else '🌿'} <b>{title}</b>",
        "",
        f"💰 <b>{price_format(price, currency)}</b>",
        DIV,
    ]

    # Характеристики у два стовпці через · 
    chars = []
    if obj.get("rooms") and obj["rooms"] != "—":
        chars.append(f"🛏 {obj['rooms']} кімн.")
    if obj.get("area") and obj["area"] != "—":
        chars.append(f"📐 {obj['area']} м²")
    if obj.get("floor") and obj["floor"] != "—":
        chars.append(f"🏢 поверх {obj['floor']}")
    if obj.get("year") and obj["year"] != "—":
        chars.append(f"🏗 {obj['year']} р.")

    # Пара характеристик в один рядок
    for i in range(0, len(chars), 2):
        if i+1 < len(chars):
            lines.append(f"{chars[i]}  {DOT}  {chars[i+1]}")
        else:
            lines.append(chars[i])

    # Локація
    if obj.get("location") == "suburbs" and obj.get("dist_km", 0) > 0:
        lines.append(f"📍 {obj.get('district', 'Передмістя')}  {DOT}  🚗 {obj['dist_km']} км від Львова")
    elif obj.get("district"):
        loc = obj["district"]
        if obj.get("street"):
            loc += f", {obj['street']}"
        lines.append(f"📍 {loc}")

    if obj.get("heating"):
        lines.append(f"🔥 {obj['heating'][:30]}")

    if not short:
        lines.append("")
        lines.append(DIV)
        if obj.get("description"):
            lines.append(f"\n📋 <b>Про об'єкт:</b>")
            lines.append(f"<i>{obj['description'][:500]}</i>")
        if obj.get("advantages"):
            lines.append(f"\n✨ <b>Переваги:</b>")
            for adv in obj["advantages"][:5]:
                if adv:
                    lines.append(f"  ▸ {adv}")
        if obj.get("kitchen"):
            lines.append(f"\n🍳 Кухня: {obj['kitchen']} м²")

    lines.append("")
    lines.append(DIV)
    lines.append(f"🆔 <code>#{obj.get('code', '—')}</code>  {DOT}  📸 {len(obj.get('photos', []))} фото")

    return "\n".join(lines)


def welcome_text() -> str:
    return (
        f"╔{'═'*28}╗\n"
        f"   🏙 <b>{AGENCY_NAME}</b>\n"
        f"╚{'═'*28}╝\n\n"
        f"Вітаю! Я ваш особистий AI‑асистент з нерухомості у <b>Львові</b> 👋\n\n"
        f"<b>Що я вмію:</b>\n"
        f"▸ Підібрати квартиру або будинок\n"
        f"▸ Показати об'єкти з фото і деталями\n"
        f"▸ Надіслати сповіщення про нові об'єкти\n"
        f"▸ Записати на перегляд\n\n"
        f"{DIV}\n"
        f"<i>Оберіть розділ 👇</i>"
    )


def search_result_text(count: int, params: str) -> str:
    if count == 0:
        return (
            f"🔍 <b>Пошук завершено</b>\n\n"
            f"{params}\n\n"
            f"{DIV}\n"
            f"😔 <b>Нічого не знайдено</b>\n\n"
            f"Підпишіться на сповіщення — і я одразу напишу,\n"
            f"як тільки з'явиться підходящий варіант! 🔔"
        )
    return (
        f"✅ <b>Знайдено {count} {'об'єкт' if count==1 else 'об'єктів' if count<5 else 'об'єктів'}</b>\n\n"
        f"{params}\n\n"
        f"{DIV}\n"
        f"<i>Показую найкращі варіанти 👇</i>"
    )


def notif_text(obj: dict) -> str:
    deal = obj.get("deal_type", "Продаж")
    currency = "₴/міс" if obj.get("search_type") == "rent_apt" else "$"
    return (
        f"🔔 <b>НОВИЙ ОБ'ЄКТ ЗА ВАШИМ ЗАПИТОМ!</b>\n\n"
        f"{format_card(obj, short=True)}\n\n"
        f"<i>Натисніть кнопку щоб дізнатись більше або записатись на перегляд</i>"
    )


def agent_lead_text(data: dict, label: str, user) -> str:
    return (
        f"{'🔥'*5}\n"
        f"<b>НОВА ЗАЯВКА — {label.upper()}</b>\n"
        f"{'🔥'*5}\n\n"
        f"👤 @{user.username or '—'}  (ID: {user.id})\n\n"
        f"📍 {data.get('address','—')}\n"
        f"📐 {data.get('details','—')}\n"
        f"💰 {data.get('price','—')}\n"
        f"📱 {data.get('phone','—')}\n\n"
        f"{DIV}\n"
        f"⏰ Клієнт чекає дзвінка!"
    )


# ══════════════════════════════════════════════════════════════════
#  БАЗА ОБ'ЄКТІВ
# ══════════════════════════════════════════════════════════════════
OBJECTS_DB: list[dict] = []

def load_objects(filepath: str = "objects.txt"):
    global OBJECTS_DB
    OBJECTS_DB = []
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} не знайдено")
        return
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    ok = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("active"):
            continue
        obj = parse_crm_line(line)
        if obj:
            OBJECTS_DB.append(obj)
            ok += 1
    print(f"✅ Завантажено {ok} об'єктів")


def filter_objects(search_type="all", district="all", rooms="all",
                   price_from=0, price_to=0, location="lviv") -> list[dict]:
    res = []
    for o in OBJECTS_DB:
        if o.get("status") != "active":
            continue
        if search_type != "all" and o.get("search_type") != search_type:
            continue
        if location == "lviv" and o.get("location") == "region":
            continue
        if location == "suburbs" and o.get("location") != "suburbs":
            continue
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
#  FSM
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


subscriptions: dict[int, dict] = {}


# ══════════════════════════════════════════════════════════════════
#  КЛАВІАТУРИ — красивий дизайн кнопок
# ══════════════════════════════════════════════════════════════════
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    # Основні категорії — зрозумілі повні назви
    kb.button(text="🏠  Орендувати квартиру",        callback_data="menu:rent_apt")
    kb.button(text="📋  Здати квартиру в оренду",    callback_data="menu:give_apt")
    kb.button(text="🔑  Купити квартиру",             callback_data="menu:buy_apt")
    kb.button(text="🏡  Купити будинок",              callback_data="menu:buy_house")
    kb.button(text="💰  Продати квартиру",            callback_data="menu:sell_apt")
    kb.button(text="🏘  Продати будинок",             callback_data="menu:sell_house")
    kb.button(text="🌿  Земельні ділянки",            callback_data="menu:land")
    kb.button(text="🔔  Сповіщення про нові об'єкти", callback_data="menu:subscribe")
    kb.button(text="📞  Зв'язатись з ріелтором",     callback_data="action:contact")
    kb.adjust(1)  # Кожна кнопка на окремому рядку — повна назва без обрізання
    return kb.as_markup()

def rooms_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Студія",    callback_data="rooms:Студія")
    kb.button(text="🛏 1 кімната", callback_data="rooms:1")
    kb.button(text="🛏 2 кімнати", callback_data="rooms:2")
    kb.button(text="🛏 3 кімнати", callback_data="rooms:3")
    kb.button(text="🛏 4+ кімнати",callback_data="rooms:4+")
    kb.button(text="🔀 Будь-яка",  callback_data="rooms:all")
    kb.button(text="◀ Назад",      callback_data="back:main")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()

def district_kb(suburbs: bool = False):
    kb = InlineKeyboardBuilder()
    if not suburbs:
        for d in ["Галицький","Сихів","Шевченківський","Залізничний","Франківський","Личаківський"]:
            kb.button(text=f"📍 {d}",    callback_data=f"district:{d}")
        kb.button(text="🏙 Всі райони Львова",  callback_data="district:all")
        kb.button(text="🌳 Передмістя (+20 км)", callback_data="location:suburbs")
    else:
        for s in ["Брюховичі","Винники","Пустомити","Рудне","Сокільники","Малехів","Зимна Вода","Давидів"]:
            kb.button(text=f"🌳 {s}", callback_data=f"suburb:{s}")
        kb.button(text="🌍 Всі передмістя",      callback_data="suburb:all")
        kb.button(text="◀ Повернутись до Львова", callback_data="district:all")
    kb.button(text="◀ Назад", callback_data="back:main")
    kb.adjust(2, 2, 2, 1, 1, 1)
    return kb.as_markup()

def freq_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚡ Одразу як з'явиться",    callback_data="freq:instant")
    kb.button(text="🌅 Зведення раз на день",   callback_data="freq:daily")
    kb.button(text="📆 Підсумок за тиждень",    callback_data="freq:weekly")
    kb.adjust(1)
    return kb.as_markup()

def obj_kb(code: str, has_map: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записатись на перегляд",   callback_data=f"view:{code}")
    kb.button(text="📸 Всі фото",                  callback_data=f"gallery:{code}")
    kb.button(text="👤 Зв'язатись з ріелтором",   callback_data="action:contact")
    kb.button(text="🔔 Сповіщати про схожі",       callback_data="menu:subscribe")
    kb.button(text="◀ Головне меню",               callback_data="back:main")
    kb.adjust(2, 1, 1, 1)
    return kb.as_markup()

def after_results_kb(count: int):
    kb = InlineKeyboardBuilder()
    if count > 3:
        kb.button(text=f"📋 Показати всі {count}", callback_data="action:show_all")
    kb.button(text="🔔 Сповіщати про нові",         callback_data="menu:subscribe")
    kb.button(text="🔄 Змінити параметри",           callback_data="back:main")
    kb.button(text="👤 Зв'язатись з ріелтором",     callback_data="action:contact")
    kb.adjust(1)
    return kb.as_markup()

def back_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Головне меню",             callback_data="back:main")
    kb.button(text="👤 Зв'язатись з ріелтором",  callback_data="action:contact")
    kb.adjust(1)
    return kb.as_markup()

def view_kb(code: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтвердити запис",         callback_data=f"confirm_view:{code}")
    kb.button(text="📞 Зателефонувати зараз",      callback_data="action:contact")
    kb.button(text="◀ Назад до об'єкту",           callback_data=f"gallery:{code}")
    kb.adjust(1)
    return kb.as_markup()


# ══════════════════════════════════════════════════════════════════
#  ВІДПРАВКА ОБ'ЄКТУ
# ══════════════════════════════════════════════════════════════════
async def send_obj(msg: Message, obj: dict, short: bool = True):
    text   = format_card(obj, short=short)
    code   = str(obj.get("code", ""))
    photos = obj.get("photos", [])
    try:
        if photos:
            await msg.answer_photo(photo=photos[0], caption=text,
                                   parse_mode="HTML", reply_markup=obj_kb(code))
        else:
            await msg.answer(text, parse_mode="HTML", reply_markup=obj_kb(code))
    except TelegramBadRequest:
        await msg.answer(text, parse_mode="HTML", reply_markup=obj_kb(code))


async def send_gallery(msg: Message, obj: dict):
    photos = obj.get("photos", [])
    code   = str(obj.get("code", ""))
    if len(photos) <= 1:
        await send_obj(msg, obj, short=False)
        return
    media = []
    for i, url in enumerate(photos[:10]):
        cap = format_card(obj, short=False) if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=cap, parse_mode="HTML"))
    try:
        await msg.answer_media_group(media=media)
        await msg.answer(
            f"📸 <b>Галерея</b> — {len(photos)} фото\n🆔 Об'єкт <code>#{code}</code>",
            parse_mode="HTML", reply_markup=obj_kb(code)
        )
    except TelegramBadRequest:
        await send_obj(msg, obj, short=False)


# ══════════════════════════════════════════════════════════════════
#  /start та команди
# ══════════════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(welcome_text(), reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        f"✅ <b>Скасовано.</b>\n\n{DIV}\nПовертаємось у головне меню:",
        reply_markup=main_menu_kb(), parse_mode="HTML"
    )

@dp.message(Command("mysub"))
async def cmd_mysub(msg: Message):
    uid = msg.from_user.id
    if uid not in subscriptions:
        await msg.answer(
            f"🔕 <b>Активних підписок немає</b>\n\n"
            f"Налаштуйте сповіщення — і я першим напишу про новий об'єкт!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔔 Налаштувати сповіщення", callback_data="menu:subscribe")
            ]])
        )
        return
    s  = subscriptions[uid]
    fl = {"instant":"одразу","daily":"раз на день","weekly":"раз на тиждень"}
    tl = {"rent_apt":"Оренда квартири","buy_apt":"Купівля квартири",
          "buy_house":"Купівля будинку","land":"Земельна ділянка","all":"Всі типи"}
    await msg.answer(
        f"🔔 <b>Ваша активна підписка</b>\n\n"
        f"{DIV}\n"
        f"🏠 {tl.get(s.get('type','all'))}\n"
        f"📍 {s.get('district','Всі райони')}\n"
        f"💰 {s.get('price_from',0):,} — {s.get('price_to',0):,} $\n"
        f"⏰ Сповіщення {fl.get(s.get('frequency','instant'))}\n"
        f"{DIV}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Змінити параметри", callback_data="menu:subscribe"),
             InlineKeyboardButton(text="❌ Вимкнути",          callback_data="action:unsub")],
            [InlineKeyboardButton(text="◀ Головне меню",       callback_data="back:main")],
        ])
    )

@dp.message(Command("add_object"))
async def cmd_add(msg: Message, state: FSMContext):
    if str(msg.from_user.id) != str(AGENT_CHAT_ID):
        return
    await state.set_state(AddObjectState.waiting_data)
    await msg.answer(
        f"📋 <b>Додати об'єкт з CRM</b>\n\n"
        f"{DIV}\n"
        f"Вставте рядок експорту з RealtSoft.\nОдин рядок = один об'єкт.",
        parse_mode="HTML"
    )

@dp.message(AddObjectState.waiting_data)
async def receive_crm(msg: Message, state: FSMContext):
    obj = parse_crm_line(msg.text)
    if not obj:
        await msg.answer("⚠️ Не вдалось розпізнати формат. Перевірте рядок.")
        return
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
        f"🆔 #{obj['code']}  📍 {obj['district']}\n"
        f"💰 {obj.get('price_usd',0):,} $  📸 {len(obj.get('photos',[]))} фото\n\n"
        f"Надіслати сповіщення підписникам?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Так, розіслати", callback_data=f"notify:{obj['code']}"),
             InlineKeyboardButton(text="⏭ Пропустити",     callback_data="back:main")],
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
    await cb.message.answer(
        f"✅ Сповіщення надіслано <b>{n}</b> підписникам!",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "back:main")
async def back_main(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(welcome_text(), reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("menu:"))
async def handle_menu(cb: CallbackQuery, state: FSMContext):
    s = cb.data.split(":")[1]
    await cb.answer()

    if s in ("rent_apt", "buy_apt"):
        cur = "₴" if s == "rent_apt" else "$"
        lbl = "🏠 <b>Оренда квартири</b>" if s == "rent_apt" else "🔑 <b>Купівля квартири</b>"
        await state.update_data(search_type=s, currency=cur)
        await cb.message.edit_text(
            f"{lbl}\n\n{DIV}\n🛏 Скільки кімнат вас цікавить?",
            parse_mode="HTML", reply_markup=rooms_kb()
        )
        await state.set_state(SearchState.choosing_rooms)

    elif s == "buy_house":
        await state.update_data(search_type="buy_house", currency="$")
        await cb.message.edit_text(
            f"🏡 <b>Купівля будинку</b>\n\n{DIV}\n📍 Оберіть район або локацію:",
            parse_mode="HTML", reply_markup=district_kb()
        )
        await state.set_state(SearchState.choosing_district)

    elif s in ("give_apt", "sell_apt", "sell_house"):
        prompts = {
            "give_apt":   f"📋 <b>Здати квартиру в оренду</b>\n\n{DIV}\n✅ Безкоштовне розміщення на 40+ майданчиках!\n✅ Ріелтор зв'яжеться протягом 15 хвилин\n\n{DIV}\n📍 Введіть адресу квартири:",
            "sell_apt":   f"💰 <b>Продати квартиру</b>\n\n{DIV}\n✅ Безкоштовна оцінка ринкової вартості\n✅ Професійна фотозйомка\n✅ Розміщення на 40+ майданчиках\n✅ Юридичний супровід угоди\n\n{DIV}\n📍 Введіть адресу квартири:",
            "sell_house": f"🏘 <b>Продати будинок</b>\n\n{DIV}\n✅ Безкоштовна оцінка\n✅ Фотосесія та відеозйомка\n✅ Перевірка документів\n✅ Юридичний супровід\n\n{DIV}\n📍 Введіть адресу будинку:",
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
        kb.button(text="🛒 Купити земельну ділянку", callback_data="land:buy")
        kb.button(text="💰 Продати земельну ділянку",callback_data="land:sell")
        kb.button(text="◀ Назад",                    callback_data="back:main")
        kb.adjust(1)
        await cb.message.edit_text(
            f"🌿 <b>Земельні ділянки</b>\n\n{DIV}\nЩо вас цікавить?",
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
    lbl = "будь-яка" if r == "all" else r
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Кімнат: <b>{lbl}</b>\n\n{DIV}\n📍 Оберіть район або локацію:",
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
    hint = "\n<i>Мінімум для оренди: 10 000 ₴</i>" if cur == "₴" else ""
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Район: <b>{'Всі райони Львова' if d=='all' else d}</b>\n\n"
        f"{DIV}\n💰 Введіть <b>мінімальну</b> ціну ({cur})\n"
        f"<i>Або <code>0</code> — без обмежень{hint}</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
        ]])
    )
    await state.set_state(SearchState.entering_price_from)

@dp.callback_query(SearchState.choosing_district, F.data == "location:suburbs")
@dp.callback_query(F.data == "location:suburbs")
async def pick_suburbs_menu(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        f"🌳 <b>Передмістя Львова</b>\n\n{DIV}\nОберіть населений пункт:",
        parse_mode="HTML", reply_markup=district_kb(suburbs=True)
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
        f"✅ Локація: <b>🌳 {lbl}</b>\n\n{DIV}\n💰 Введіть <b>мінімальну</b> ціну ({cur})\n<i>Або <code>0</code> — без обмежень</i>",
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
        await msg.answer("⚠️ Введіть число, наприклад: <code>50000</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    pf = int(t)
    if data.get("currency") == "₴" and 0 < pf < 10000:
        await msg.answer("⚠️ Мінімум оренди <b>10 000 ₴</b>. Введіть ще раз:", parse_mode="HTML")
        return
    await state.update_data(price_from=pf)
    cur = data.get("currency","$")
    await msg.answer(
        f"✅ Від: <b>{pf:,} {cur}</b>\n\n{DIV}\n💰 Введіть <b>максимальну</b> ціну ({cur})\n<i>Або <code>0</code> — без обмежень</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchState.entering_price_to)

@dp.message(SearchState.entering_price_to)
async def spt(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Введіть число, наприклад: <code>150000</code>", parse_mode="HTML")
        return
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

    lbl = {"rent_apt":"Оренда квартири","buy_apt":"Купівля квартири",
           "buy_house":"Купівля будинку","land":"Ділянки"}
    pr  = f"{pf:,} – {pt:,} {cur}" if pt else f"від {pf:,} {cur}"
    loc = f"🌳 {'Всі передмістя' if suburb=='all' else suburb}" if location=="suburbs" else f"📍 {'Всі райони' if dist=='all' else dist}"

    params_str = (
        f"🏠 {lbl.get(stype,stype)}\n"
        f"🛏 {'Будь-яка кімнатність' if rooms=='all' else rooms+' кімн.'}\n"
        f"{loc}\n"
        f"💰 {pr}"
    )

    await msg.answer(
        f"🔍 <b>Шукаю...</b>\n\n{params_str}\n\n<i>⏳ Перевіряю базу об'єктів</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(1.0)

    results = filter_objects(search_type=stype, district=dist, rooms=rooms,
                             price_from=pf, price_to=pt, location=location)
    if location == "suburbs" and suburb != "all":
        results = [o for o in results if suburb.lower() in
                   (o.get("street","") + " " + o.get("district","") + " " + o.get("raw","")).lower()]

    await msg.answer(search_result_text(len(results), params_str), parse_mode="HTML")

    if not results:
        await msg.answer(
            "Спробуйте розширити параметри або підпишіться на сповіщення:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="menu:subscribe")],
                [InlineKeyboardButton(text="🔄 Змінити параметри",         callback_data="back:main")],
            ])
        )
        return

    for obj in results[:3]:
        await asyncio.sleep(0.3)
        await send_obj(msg, obj, short=True)

    await msg.answer(
        f"<i>Знайдено {len(results)} об'єктів</i>",
        parse_mode="HTML",
        reply_markup=after_results_kb(len(results))
    )


# ══════════════════════════════════════════════════════════════════
#  ПЕРЕГЛЯД + ГАЛЕРЕЯ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("gallery:"))
async def cb_gallery(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    await cb.answer()
    obj = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    if obj:
        await send_gallery(cb.message, obj)
    else:
        await cb.message.answer("Об'єкт не знайдено в базі", reply_markup=back_kb())

@dp.callback_query(F.data.startswith("view:"))
async def cb_view(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    await cb.answer()
    obj = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    obj_title = obj.get("title","")[:40] if obj else f"#{code}"
    await cb.message.answer(
        f"📅 <b>Запис на перегляд</b>\n\n"
        f"🏠 {obj_title}\n\n"
        f"{DIV}\n"
        f"Оберіть зручний час:\n\n"
        f"📆 <b>Завтра</b>\n"
        f"  · 10:00  · 12:00  · 14:00  · 16:00  · 18:00\n\n"
        f"📆 <b>Будь-який інший день</b>\n"
        f"  Пн–Пт з 9:00 до 20:00\n\n"
        f"{DIV}\n"
        f"Напишіть зручний час або одразу зателефонуйте:\n"
        f"📱 <b>{AGENT_PHONE}</b>",
        parse_mode="HTML",
        reply_markup=view_kb(code)
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"👁 <b>ЗАПИТ НА ПЕРЕГЛЯД</b>\n🆔 #{code}\n"
            f"👤 @{cb.from_user.username or '—'} · {cb.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith("confirm_view:"))
async def confirm_view(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    await cb.answer("✅ Записано!")
    await cb.message.edit_text(
        f"✅ <b>Вас записано на перегляд!</b>\n\n"
        f"{DIV}\n"
        f"Ріелтор зателефонує вам для підтвердження часу.\n\n"
        f"📱 Якщо хочете зв'язатись зараз:\n<b>{AGENT_PHONE}</b>",
        parse_mode="HTML",
        reply_markup=back_kb()
    )


# ══════════════════════════════════════════════════════════════════
#  ЗЕМЕЛЬНІ ДІЛЯНКИ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "land:buy")
async def land_buy(cb: CallbackQuery, state: FSMContext):
    await state.update_data(search_type="land", currency="$")
    await cb.answer()
    await cb.message.edit_text(
        f"🌿 <b>Купівля земельної ділянки</b>\n\n{DIV}\n📍 Оберіть напрямок:",
        parse_mode="HTML", reply_markup=district_kb()
    )
    await state.set_state(SearchState.choosing_district)

@dp.callback_query(F.data == "land:sell")
async def land_sell(cb: CallbackQuery, state: FSMContext):
    await state.update_data(sell_type="land_sell")
    await state.set_state(SellState.entering_address)
    await cb.answer()
    await cb.message.edit_text(
        f"🌿 <b>Продати земельну ділянку</b>\n\n{DIV}\n"
        f"✅ Безкоштовна оцінка\n✅ Перевірка документів\n✅ Розміщення на майданчиках\n\n{DIV}\n"
        f"📍 Введіть адресу або напрямок ділянки:",
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
    prompts = {
        "land_sell":  f"📐 Площа ділянки та наявні комунікації:\n<i>Наприклад: 10 соток, газ + електрика</i>",
        "sell_house": f"📐 Площа будинку, кількість поверхів, площа ділянки:\n<i>Наприклад: 180 м², 2 поверхи, 12 соток</i>",
    }
    await msg.answer(
        prompts.get(stype, f"📐 Площа, поверх, рік побудови:\n<i>Наприклад: 58 м², 7/12, 2022 рік</i>"),
        parse_mode="HTML"
    )
    await state.set_state(SellState.entering_details)

@dp.message(SellState.entering_details)
async def sell_det(msg: Message, state: FSMContext):
    await state.update_data(details=msg.text)
    await msg.answer(
        f"💰 Бажана ціна:\n<i>Або напишіть «оцінка» — ріелтор оцінить безкоштовно</i>",
        parse_mode="HTML"
    )
    await state.set_state(SellState.entering_price)

@dp.message(SellState.entering_price)
async def sell_pr(msg: Message, state: FSMContext):
    await state.update_data(price=msg.text)
    await msg.answer("📱 Ваш номер телефону для зв'язку:")
    await state.set_state(SellState.entering_phone)

@dp.message(SellState.entering_phone)
async def sell_ph(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    data  = await state.get_data()
    lbls  = {"sell_apt":"Продаж квартири","sell_house":"Продаж будинку","land_sell":"Продаж ділянки"}
    label = lbls.get(data.get("sell_type",""), "Здача в оренду")
    await msg.answer(
        f"✅ <b>Заявку отримано!</b>\n\n"
        f"{DIV}\n"
        f"📋 {label}\n"
        f"📍 {data.get('address','—')}\n"
        f"📐 {data.get('details','—')}\n"
        f"💰 {data.get('price','—')}\n"
        f"📱 {data.get('phone','—')}\n"
        f"{DIV}\n\n"
        f"⏰ Ріелтор зателефонує протягом <b>15 хвилин</b>!",
        parse_mode="HTML", reply_markup=back_kb()
    )
    try:
        await bot.send_message(AGENT_CHAT_ID,
            agent_lead_text(data, label, msg.from_user), parse_mode="HTML")
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
    await cb.message.answer(
        f"👤 <b>Зв'язок з ріелтором</b>\n\n{DIV}\nВведіть ваше ім'я:",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ContactState.entering_name)
async def cn(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("📱 Ваш номер телефону:")
    await state.set_state(ContactState.entering_phone)

@dp.message(ContactState.entering_phone)
async def cp(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    await msg.answer(
        f"💬 Що вас цікавить?\n<i>Наприклад: 2-кімн. у Сихові до $70k</i>",
        parse_mode="HTML"
    )
    await state.set_state(ContactState.entering_comment)

@dp.message(ContactState.entering_comment)
async def cmt(msg: Message, state: FSMContext):
    await state.update_data(comment=msg.text)
    data = await state.get_data()
    await msg.answer(
        f"✅ <b>Дякую, {data.get('name','')}!</b>\n\n"
        f"{DIV}\n"
        f"Ріелтор зателефонує вам на <b>{data.get('phone','')}</b>\n"
        f"протягом <b>15 хвилин</b>.\n\n"
        f"Або самі зателефонуйте:\n📱 <b>{AGENT_PHONE}</b>",
        parse_mode="HTML", reply_markup=back_kb()
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"📞 <b>ЗАПИТ НА ЗВ'ЯЗОК</b>\n\n"
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
    t = (
        f"🔔 <b>Сповіщення про нові об'єкти</b>\n\n"
        f"{DIV}\n"
        f"Я буду <b>першим</b> надсилати вам нові об'єкти\n"
        f"та сповіщати про зниження цін!\n\n"
        f"{DIV}\n"
        f"Оберіть тип нерухомості:"
    )
    if edit:
        await msg.edit_text(t, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await msg.answer(t, parse_mode="HTML", reply_markup=kb.as_markup())
    await state.set_state(SubscribeState.choosing_type)

@dp.callback_query(SubscribeState.choosing_type, F.data.startswith("sub_type:"))
async def sub_type(cb: CallbackQuery, state: FSMContext):
    st  = cb.data.split(":")[1]
    cur = "₴" if st == "rent_apt" else "$"
    await state.update_data(sub_type=st, currency=cur)
    await cb.answer()
    await cb.message.edit_text(
        f"🔔 Сповіщення\n\n{DIV}\n📍 Оберіть район:",
        parse_mode="HTML", reply_markup=district_kb()
    )
    await state.set_state(SubscribeState.choosing_district)

@dp.callback_query(SubscribeState.choosing_district, F.data.startswith("district:"))
async def sub_dist(cb: CallbackQuery, state: FSMContext):
    d = cb.data.split(":")[1]
    await state.update_data(sub_district=d, sub_location="lviv")
    data = await state.get_data()
    cur  = data.get("currency","$")
    hint = "\n<i>Мінімум 10 000 ₴</i>" if cur == "₴" else ""
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Район: <b>{'Всі райони Львова' if d=='all' else d}</b>\n\n"
        f"{DIV}\n💰 Мінімальний бюджет ({cur})\n<i><code>0</code> — без обмежень{hint}</i>",
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
        f"{DIV}\n💰 Мінімальний бюджет ({cur})\n<i><code>0</code> — без обмежень</i>",
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
        await msg.answer("⚠️ Введіть число:"); return
    data = await state.get_data()
    pf   = int(t)
    if data.get("currency") == "₴" and 0 < pf < 10000:
        await msg.answer("⚠️ Мінімум <b>10 000 ₴</b>:", parse_mode="HTML"); return
    await state.update_data(sub_price_from=pf)
    await msg.answer(
        f"✅ Від <b>{pf:,}</b>\n\n{DIV}\n💰 Максимальний бюджет\n<i><code>0</code> — без обмежень</i>",
        parse_mode="HTML"
    )
    await state.set_state(SubscribeState.entering_price_to)

@dp.message(SubscribeState.entering_price_to)
async def sub_pt(msg: Message, state: FSMContext):
    t = msg.text.strip().replace(" ","").replace(",","")
    if not t.isdigit():
        await msg.answer("⚠️ Введіть число:"); return
    await state.update_data(sub_price_to=int(t))
    await msg.answer(
        f"⏰ <b>Як часто надсилати сповіщення?</b>\n\n{DIV}",
        parse_mode="HTML", reply_markup=freq_kb()
    )
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
    fl = {"instant":"одразу ⚡","daily":"раз на день 🌅","weekly":"раз на тиждень 📆"}
    tl = {"rent_apt":"Оренда кв.","buy_apt":"Купівля кв.","buy_house":"Купівля будинку",
          "land":"Ділянка","all":"Всі типи"}
    cur = data.get("currency","$")
    pt  = data.get("sub_price_to",0)
    pr  = f"{data.get('sub_price_from',0):,} – {pt:,} {cur}" if pt else f"від {data.get('sub_price_from',0):,} {cur}"
    await cb.answer()
    await cb.message.edit_text(
        f"🔔 <b>Сповіщення увімкнено!</b>\n\n"
        f"{DIV}\n"
        f"🏠 {tl.get(data.get('sub_type','all'))}\n"
        f"📍 {'Всі райони' if data.get('sub_district')=='all' else data.get('sub_district')}\n"
        f"💰 {pr}\n"
        f"⏰ {fl.get(freq)}\n"
        f"{DIV}\n\n"
        f"Я <b>першим</b> надішлю вам нові об'єкти!\n"
        f"<i>Керувати підпискою: /mysub</i>",
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
        f"🔕 <b>Сповіщення вимкнено</b>\n\n{DIV}\nМожна увімкнути знову будь-коли.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Увімкнути знову", callback_data="menu:subscribe"),
             InlineKeyboardButton(text="🏠 Меню",            callback_data="back:main")],
        ])
    )


# ══════════════════════════════════════════════════════════════════
#  РОЗСИЛКА — НОВИЙ ОБ'ЄКТ
# ══════════════════════════════════════════════════════════════════
async def broadcast_new_object(obj: dict) -> int:
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
        kb = InlineKeyboardBuilder()
        kb.button(text="📅 Записатись на перегляд", callback_data=f"view:{obj.get('code','')}")
        kb.button(text="📸 Переглянути фото",        callback_data=f"gallery:{obj.get('code','')}")
        kb.button(text="👤 Зв'язатись з ріелтором",  callback_data="action:contact")
        kb.adjust(1)
        try:
            photos = obj.get("photos", [])
            text   = notif_text(obj)
            if photos:
                await bot.send_photo(uid, photo=photos[0], caption=text,
                                     parse_mode="HTML", reply_markup=kb.as_markup())
            else:
                await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb.as_markup())
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
    await msg.answer(
        f"🏠 Оберіть дію з меню:",
        reply_markup=main_menu_kb()
    )


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════
async def main():
    load_objects("objects.txt")
    print(f"🤖 {AGENCY_NAME} Bot запущено. Об'єктів: {len(OBJECTS_DB)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
