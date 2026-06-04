import asyncio
import re
import os
import aiohttp
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

TOKEN         = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
AGENT_CHAT_ID = os.getenv("AGENT_CHAT_ID", "YOUR_ID")
AGENT_PHONE   = os.getenv("AGENT_PHONE", "+38 067 123 45 67")
AGENCY_NAME   = os.getenv("AGENCY_NAME", "Empire Capital Lviv")
BANNER_URL    = os.getenv("BANNER_URL", "")

# ── RealtSoft CRM webhook (опціонально) ────────────────────────
CRM_WEBHOOK_URL = os.getenv("CRM_WEBHOOK_URL", "")
CRM_API_KEY     = os.getenv("CRM_API_KEY", "")

bot     = Bot(token=TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)


# ══════════════════════════════════════════════════════════════════
#  CRM ІНТЕГРАЦІЯ — надсилання заявок у RealtSoft
# ══════════════════════════════════════════════════════════════════
async def send_to_crm(data: dict) -> bool:
    """
    Надсилає заявку клієнта у RealtSoft CRM через webhook.
    Налаштуйте в CRM: Налаштування → Інтеграції → Webhook
    Вставте URL webhook у змінну CRM_WEBHOOK_URL на Railway.
    """
    if not CRM_WEBHOOK_URL:
        return False
    try:
        payload = {
            "source": f"Telegram Bot — {AGENCY_NAME}",
            "name":    data.get("name", ""),
            "phone":   data.get("phone", ""),
            "comment": data.get("comment", ""),
            "type":    data.get("type", ""),
            "address": data.get("address", ""),
            "budget":  data.get("budget", ""),
            "api_key": CRM_API_KEY,
        }
        headers = {"Content-Type": "application/json"}
        if CRM_API_KEY:
            headers["Authorization"] = f"Bearer {CRM_API_KEY}"

        async with aiohttp.ClientSession() as session:
            async with session.post(CRM_WEBHOOK_URL, json=payload,
                                    headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status in (200, 201, 204)
    except Exception as e:
        print(f"CRM webhook error: {e}")
        return False


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

    obj = {"status": parts[0], "code": parts[2], "deal_type": parts[3], "raw": raw}

    obj["photos"] = re.findall(r'https://[^\s]+/estate-images/watermark/[^\s]+\.jpg', raw)

    # Ціна (\x24 = $ для коректного парсингу)
    pm = re.search(r'Ціна:\s*([\d\s]+?)\s*\x24', raw)
    if pm:
        obj["price_usd"] = int(pm.group(1).replace(" ", ""))
    else:
        pm2 = re.search(r'(\d[\d ]+?)\s*\x24', raw)
        obj["price_usd"] = int(pm2.group(1).replace(" ", "")) if pm2 else 0

    code_m = re.search(r'Код\s+#(\d+)', raw)
    if code_m:
        obj["code"] = code_m.group(1)

    area_m = re.search(r'Площа:\s*([\d,\.]+)\s*м²', raw)
    obj["area"] = area_m.group(1).replace(",", ".") if area_m else ""

    floor_m = re.search(r'Поверх:\s*(\d+)\s*з\s*(\d+)', raw)
    obj["floor"]        = f"{floor_m.group(1)}/{floor_m.group(2)}" if floor_m else ""
    obj["floors_total"] = int(floor_m.group(2)) if floor_m else 0

    rooms_m = re.search(r'(\d+)-(?:к\b|кімнатн)', raw)
    obj["rooms"] = rooms_m.group(1) if rooms_m else ""

    dist_m = re.search(
        r'(Личаківськ\w*|Сихівськ\w*|Галицьк\w*|Шевченківськ\w*|Залізничн\w*|Франківськ\w*|Сихів)\s*(?:район)?',
        raw, re.IGNORECASE
    )
    raw_d = dist_m.group(1) if dist_m else "Львів"
    norm  = {"личаківськ":"Личаківський","сихівськ":"Сихівський","галицьк":"Галицький",
             "шевченківськ":"Шевченківський","залізничн":"Залізничний","франківськ":"Франківський","сихів":"Сихів"}
    obj["district"] = next((v for k,v in norm.items() if raw_d.lower().startswith(k)), raw_d)

    street_m = re.search(r'вул\.\s+([^\,\.\n]+)', raw)
    obj["street"] = street_m.group(1).strip() if street_m else ""

    coords = re.findall(r'(4[89]\.\d{5,})\s+(2[234]\.\d{5,})', raw)
    obj["lat"] = coords[0][0] if coords else ""
    obj["lng"] = coords[0][1] if coords else ""

    year_m = re.search(r'(\d{4})\s+року', raw)
    obj["year"] = year_m.group(1) if year_m else ""

    heat_m = re.search(r'Опалення:\s*([^\n\(]+)', raw)
    obj["heating"] = heat_m.group(1).strip() if heat_m else ""

    phone_m = re.search(r'(0\d{9})', raw)
    obj["agent_phone"] = phone_m.group(1) if phone_m else ""

    agency_m = re.search(r'([A-ZА-ЯЇІЄҐ][a-zA-Zа-яїієґ\s]{2,}(?:Capital|Estate|Agency|Realty|Нерухомість))', raw)
    obj["agency"] = agency_m.group(1).strip() if agency_m else ""

    title_m = re.search(r'(?:Продаж|Оренда)\s+(.+?)(?=\s{2,}|\s+Пропонується|\s+Здається)', raw)
    obj["title"] = title_m.group(1).strip() if title_m else f"{obj['deal_type']} {obj['rooms']}-кімн., {obj['district']}"

    desc_m = re.search(r'(Пропонується.+?)(?=Локація:|Площа:|Поверх:|Переваги:)', raw, re.DOTALL)
    obj["description"] = desc_m.group(1).strip()[:400] if desc_m else ""

    adv_m = re.search(r'Переваги:(.*?)(?=Поруч|Ідеально|Телефонуйте|$)', raw, re.DOTALL)
    if adv_m:
        obj["advantages"] = [l.strip().lstrip("–-•").strip()
                             for l in adv_m.group(1).split("\n")
                             if l.strip().lstrip("–-• ")][:5]
    else:
        obj["advantages"] = []

    raw_lower = raw.lower()
    obj["property_type"] = ("house" if "будинок" in raw_lower or "будинку" in raw_lower
                            else "land" if "ділянк" in raw_lower else "apartment")

    obj["search_type"] = ("rent_apt" if "Оренда" in obj["deal_type"]
                          else "buy_house" if obj["property_type"] == "house"
                          else "land" if obj["property_type"] == "land"
                          else "buy_apt")

    obj["location"] = "lviv"
    obj["dist_km"]  = 0
    if obj.get("lat") and obj.get("lng"):
        try:
            lat, lng = float(obj["lat"]), float(obj["lng"])
            dist = round(((abs(lat-49.8397)*111)**2 + (abs(lng-24.0297)*111*0.63)**2)**0.5, 1)
            obj["dist_km"] = dist
            obj["location"] = "lviv" if dist <= 5 else ("suburbs" if dist <= 20 else "region")
        except Exception:
            pass
    return obj


def fmt_price(price: int, search_type: str = "buy_apt") -> str:
    if not price:
        return "Ціна договірна"
    s = f"{price:,}".replace(",", " ")
    return f"{s} {'₴/міс' if search_type == 'rent_apt' else '$'}"


def card_caption(obj: dict, full: bool = False) -> str:
    """Картка у стилі AVANGARD — чисто, мінімально."""
    lines = []

    # ID + кімнати + площа
    rooms = obj.get("rooms","")
    area  = obj.get("area","")
    floor = obj.get("floor","")
    dist  = obj.get("district","")
    street= obj.get("street","")
    price = obj.get("price_usd", 0)
    year  = obj.get("year","")
    heat  = obj.get("heating","")

    lines.append(f"⚡ <b>ID {obj.get('code','')}</b>")
    if rooms: lines.append(f"🔑 {rooms} к")
    if area:  lines.append(f"◻️ {area} м²")
    if floor: lines.append(f"🏢 поверх {floor}")
    if dist:  lines.append(f"📍 {dist}")
    if street:lines.append(f"📌 {street}")
    if price: lines.append(f"💰 {fmt_price(price, obj.get('search_type','buy_apt'))}")

    if obj.get("agent_phone"):
        lines.append(f"📞 +38{obj['agent_phone']}")

    if full:
        if year:
            lines.append(f"🏗 {year} р. побудови")
        if heat:
            lines.append(f"🔥 {heat[:35]}")
        if obj.get("description"):
            lines.append(f"\n{obj['description'][:400]}")
        if obj.get("advantages"):
            lines.append("\n✅ Переваги:")
            for a in obj["advantages"]:
                if a: lines.append(f"  · {a}")

    return "\n".join(lines)


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
        for line in f:
            line = line.strip()
            if line and line.startswith("active"):
                obj = parse_crm_line(line)
                if obj:
                    OBJECTS_DB.append(obj)
    print(f"✅ Завантажено {len(OBJECTS_DB)} об'єктів")


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
        if rooms not in ("all", ""):
            if rooms == "4+":
                try:
                    if int(o.get("rooms", 0)) < 4: continue
                except: continue
            elif o.get("rooms") != rooms:
                continue
        p = o.get("price_usd", 0)
        if price_from > 0 and p < price_from: continue
        if price_to   > 0 and p > price_to:   continue
        res.append(o)
    return res


# ══════════════════════════════════════════════════════════════════
#  FSM
# ══════════════════════════════════════════════════════════════════
class BuyState(StatesGroup):
    choosing_type     = State()
    choosing_rooms    = State()
    choosing_district = State()
    choosing_budget   = State()

class RentState(StatesGroup):
    choosing_rooms    = State()
    choosing_district = State()
    choosing_budget   = State()

class SellState(StatesGroup):
    entering_address = State()
    entering_details = State()
    entering_price   = State()
    entering_phone   = State()

class ContactState(StatesGroup):
    entering_phone   = State()
    entering_comment = State()

class ViewState(StatesGroup):
    entering_time    = State()

class AddObjectState(StatesGroup):
    waiting_data = State()


subscriptions: dict[int, dict] = {}


# ══════════════════════════════════════════════════════════════════
#  КЛАВІАТУРИ — стиль AVANGARD
# ══════════════════════════════════════════════════════════════════
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Орендувати",   callback_data="act:rent")
    kb.button(text="Купити",       callback_data="act:buy")
    kb.button(text="Здати",        callback_data="act:give")
    kb.button(text="Продати",      callback_data="act:sell")
    kb.button(text="🌿 Земельні ділянки", callback_data="act:land")
    kb.button(text="🔔 Мої сповіщення",  callback_data="act:mysub")
    kb.button(text="📞 Зв\\'язок з ріелтором", callback_data="act:contact")
    kb.adjust(2, 2, 1, 1, 1)
    return kb.as_markup()

def property_type_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Квартиру", callback_data="ptype:apt")
    kb.button(text="🏡 Будинок",  callback_data="ptype:house")
    kb.button(text="◀ Назад",     callback_data="back:main")
    kb.adjust(2, 1)
    return kb.as_markup()

def rooms_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="1",      callback_data="rooms:1")
    kb.button(text="2",      callback_data="rooms:2")
    kb.button(text="3",      callback_data="rooms:3")
    kb.button(text="4+",     callback_data="rooms:4+")
    kb.button(text="Студія", callback_data="rooms:Студія")
    kb.button(text="Будь-яка", callback_data="rooms:all")
    kb.adjust(4, 2)
    return kb.as_markup()

def district_kb(suburbs: bool = False):
    kb = InlineKeyboardBuilder()
    if not suburbs:
        for d in ["Галицький","Залізничний","Личаківський","Сихівський","Франківський","Шевченківський"]:
            kb.button(text=d, callback_data=f"district:{d}")
        kb.button(text="Всі райони", callback_data="district:all")
        kb.button(text="🌳 Передмістя (+20 км)", callback_data="location:suburbs")
    else:
        for s in ["Брюховичі","Винники","Пустомити","Рудне","Сокільники","Малехів","Зимна Вода","Давидів"]:
            kb.button(text=s, callback_data=f"suburb:{s}")
        kb.button(text="Всі передмістя",         callback_data="suburb:all")
        kb.button(text="◀ Повернутись до Львова", callback_data="location:lviv")
    kb.adjust(2)
    return kb.as_markup()

def budget_rent_kb():
    kb = InlineKeyboardBuilder()
    for label, val in [
        ("0 – 9 000 ₴",    "0:9000"),
        ("9 000 – 15 000 ₴","9000:15000"),
        ("15 000 – 20 000 ₴","15000:20000"),
        ("20 000 – 35 000 ₴","20000:35000"),
        ("35 000 – 70 000 ₴","35000:70000"),
        ("більше 70 000 ₴",  "70000:0"),
    ]:
        kb.button(text=label, callback_data=f"budget:{val}")
    kb.button(text="✅ Почати пошук", callback_data="budget:0:0")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()

def budget_buy_kb():
    kb = InlineKeyboardBuilder()
    for label, val in [
        ("до 30 000 $",      "0:30000"),
        ("30 000 – 50 000 $","30000:50000"),
        ("50 000 – 80 000 $","50000:80000"),
        ("80 000 – 120 000 $","80000:120000"),
        ("120 000 – 200 000 $","120000:200000"),
        ("більше 200 000 $",  "200000:0"),
    ]:
        kb.button(text=label, callback_data=f"budget:{val}")
    kb.button(text="✅ Шукати у всіх цінах", callback_data="budget:0:0")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()

def obj_kb(code: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записатись на перегляд", callback_data=f"view:{code}")
    kb.button(text="💾 Зберегти",               callback_data=f"save:{code}")
    kb.button(text="📸 Всі фото",               callback_data=f"gallery:{code}")
    kb.adjust(2, 1)
    return kb.as_markup()

def after_search_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔔 Підписатись на нові",    callback_data="act:subscribe")
    kb.button(text="🔄 Змінити параметри",      callback_data="back:main")
    kb.button(text="📞 Зв\\'язок з ріелтором", callback_data="act:contact")
    kb.adjust(1)
    return kb.as_markup()

def sub_active_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Моя підписка",       callback_data="sub:view")
    kb.button(text="Змінити запит",      callback_data="act:subscribe")
    kb.button(text="Призупинити підписку",callback_data="sub:pause")
    kb.button(text="Залишити номер телефону", callback_data="act:contact")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


# ══════════════════════════════════════════════════════════════════
#  ВІДПРАВКА ОБ'ЄКТІВ
# ══════════════════════════════════════════════════════════════════
async def send_obj_card(msg: Message, obj: dict):
    code   = str(obj.get("code",""))
    photos = obj.get("photos", [])
    cap    = card_caption(obj, full=False)
    try:
        if photos:
            await msg.answer_photo(photo=photos[0], caption=cap,
                                   parse_mode="HTML", reply_markup=obj_kb(code))
        else:
            await msg.answer(cap, parse_mode="HTML", reply_markup=obj_kb(code))
    except TelegramBadRequest:
        await msg.answer(cap, parse_mode="HTML", reply_markup=obj_kb(code))


async def send_gallery(msg: Message, obj: dict):
    photos = obj.get("photos", [])
    code   = str(obj.get("code",""))
    if len(photos) <= 1:
        await send_obj_card(msg, obj)
        return
    media = [InputMediaPhoto(
        media=url,
        caption=card_caption(obj, full=True) if i == 0 else None,
        parse_mode="HTML"
    ) for i, url in enumerate(photos[:10])]
    try:
        await msg.answer_media_group(media=media)
        await msg.answer(
            f"📸 {len(photos)} фото  ·  ID {code}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 Записатись на перегляд", callback_data=f"view:{code}")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="back:main")],
            ])
        )
    except TelegramBadRequest:
        await send_obj_card(msg, obj)


# ══════════════════════════════════════════════════════════════════
#  ВІТАННЯ
# ══════════════════════════════════════════════════════════════════
GREET = (
    "Вітаю 👋\n"
    f"Я Ваш персональний асистент — <b>{AGENCY_NAME}</b>.\n\n"
    "Я допоможу знайти нерухомість, записати на перегляд або розмістити об'єкт.\n\n"
    "Що Вас цікавить?"
)

GREET_RETURNING = (
    "З поверненням! 👋\n\n"
    "Ваша підписка активна.\n"
    "Ви можете переглянути або змінити запит на пошук чи призупинити підписку за допомогою меню нижче.\n"
    "Також Ви можете залишити номер телефону і наші спеціалісти допоможуть Вам знайти нерухомість."
)


# ══════════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id

    # Якщо є активна підписка — показуємо спеціальне вітання
    if uid in subscriptions:
        await msg.answer(GREET_RETURNING, parse_mode="HTML", reply_markup=sub_active_kb())
        return

    # Варіант з банером (якщо задано BANNER_URL)
    if BANNER_URL:
        try:
            await msg.answer_photo(photo=BANNER_URL, caption=GREET,
                                   parse_mode="HTML", reply_markup=main_menu_kb())
            return
        except Exception:
            pass

    await msg.answer(GREET, parse_mode="HTML", reply_markup=main_menu_kb())


@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Головне меню:", reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "back:main")
async def back_main(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    try:
        await cb.message.edit_text(GREET, parse_mode="HTML", reply_markup=main_menu_kb())
    except TelegramBadRequest:
        await cb.message.answer(GREET, parse_mode="HTML", reply_markup=main_menu_kb())


@dp.callback_query(F.data.startswith("act:"))
async def handle_act(cb: CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[1]
    await cb.answer()

    if action == "rent":
        await state.update_data(search_type="rent_apt", currency="₴")
        await cb.message.edit_text(
            "🏠 <b>Оренда квартири</b>\n\nЯку кількість кімнат розглядаєте?\nДо речі, можна обрати декілька варіантів 😉",
            parse_mode="HTML", reply_markup=rooms_kb()
        )
        await state.set_state(RentState.choosing_rooms)

    elif action == "buy":
        await cb.message.edit_text(
            "🔑 <b>Що саме вас цікавить?</b>",
            parse_mode="HTML", reply_markup=property_type_kb()
        )

    elif action in ("give", "sell"):
        type_map = {"give": "give_apt", "sell": "sell_apt"}
        prompts  = {
            "give": "📋 <b>Здати квартиру</b>\n\n✅ Безкоштовне розміщення на 40+ майданчиках\n✅ Ріелтор зв'яжеться протягом 15 хвилин\n\n📍 Введіть адресу:",
            "sell": "💰 <b>Продати нерухомість</b>\n\n✅ Безкоштовна оцінка\n✅ Фотосесія і реклама\n✅ Юридичний супровід\n\n📍 Введіть адресу:",
        }
        await state.update_data(sell_type=type_map[action])
        await state.set_state(SellState.entering_address)
        await cb.message.edit_text(
            prompts[action], parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back:main")
            ]])
        )

    elif action == "land":
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Купити ділянку",  callback_data="land:buy")
        kb.button(text="💰 Продати ділянку", callback_data="land:sell")
        kb.button(text="◀ Назад",            callback_data="back:main")
        kb.adjust(2, 1)
        await cb.message.edit_text(
            "🌿 <b>Земельні ділянки</b>\n\nЩо Вас цікавить?",
            parse_mode="HTML", reply_markup=kb.as_markup()
        )

    elif action == "mysub":
        uid = cb.from_user.id
        if uid not in subscriptions:
            await cb.message.edit_text(
                "🔕 Активних підписок немає.\n\nНалаштуйте — і я першим напишу про новий об'єкт!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔔 Налаштувати сповіщення", callback_data="act:subscribe")],
                    [InlineKeyboardButton(text="◀ Назад", callback_data="back:main")],
                ])
            )
        else:
            await cb.message.edit_text(
                GREET_RETURNING, parse_mode="HTML", reply_markup=sub_active_kb()
            )

    elif action == "subscribe":
        await start_subscribe(cb.message, state, edit=True)

    elif action == "contact":
        await state.set_state(ContactState.entering_phone)
        await cb.message.answer(
            "📞 <b>Зв'язок з ріелтором</b>\n\nВведіть Ваш номер телефону — і ми зателефонуємо:",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )


# ══════════════════════════════════════════════════════════════════
#  КУПИТИ — вибір типу
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("ptype:"))
async def pick_ptype(cb: CallbackQuery, state: FSMContext):
    ptype = cb.data.split(":")[1]
    stype = "buy_apt" if ptype == "apt" else "buy_house"
    await state.update_data(search_type=stype, currency="$")
    await cb.answer()
    await cb.message.edit_text(
        "🛏 Скільки кімнат вас цікавить?",
        reply_markup=rooms_kb()
    )
    await state.set_state(BuyState.choosing_rooms)


# ══════════════════════════════════════════════════════════════════
#  ВИБІР КІМНАТ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(BuyState.choosing_rooms, F.data.startswith("rooms:"))
@dp.callback_query(RentState.choosing_rooms, F.data.startswith("rooms:"))
async def pick_rooms(cb: CallbackQuery, state: FSMContext):
    rooms = cb.data.split(":")[1]
    await state.update_data(rooms=rooms)
    await cb.answer()
    await cb.message.edit_text(
        "📍 У якому районі розглядаєте нерухомість?",
        reply_markup=district_kb()
    )
    cur_state = await state.get_state()
    if "BuyState" in str(cur_state):
        await state.set_state(BuyState.choosing_district)
    else:
        await state.set_state(RentState.choosing_district)


# ══════════════════════════════════════════════════════════════════
#  ВИБІР РАЙОНУ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("district:"))
async def pick_district(cb: CallbackQuery, state: FSMContext):
    d = cb.data.split(":")[1]
    await state.update_data(district=d, location="lviv")
    await cb.answer()
    data = await state.get_data()
    currency = data.get("currency", "$")
    bkb = budget_rent_kb() if currency == "₴" else budget_buy_kb()
    lbl = "Всі райони Львова" if d == "all" else d
    await cb.message.edit_text(
        f"✅ Район: <b>{lbl}</b>\n\nТепер визначимось з бюджетом і найкращі пропозиції поступлять у наш чат 🥂",
        parse_mode="HTML", reply_markup=bkb
    )
    cur_state = await state.get_state()
    if "BuyState" in str(cur_state):
        await state.set_state(BuyState.choosing_budget)
    else:
        await state.set_state(RentState.choosing_budget)

@dp.callback_query(F.data == "location:suburbs")
async def pick_suburbs(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "🌳 <b>Передмістя Львова</b>\n\nОберіть населений пункт:",
        parse_mode="HTML", reply_markup=district_kb(suburbs=True)
    )

@dp.callback_query(F.data == "location:lviv")
async def back_to_lviv(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "📍 У якому районі розглядаєте нерухомість?",
        reply_markup=district_kb()
    )

@dp.callback_query(F.data.startswith("suburb:"))
async def pick_suburb(cb: CallbackQuery, state: FSMContext):
    suburb = cb.data.split(":")[1]
    await state.update_data(district="all", location="suburbs", suburb=suburb)
    await cb.answer()
    data = await state.get_data()
    currency = data.get("currency", "$")
    bkb = budget_rent_kb() if currency == "₴" else budget_buy_kb()
    lbl = "Всі передмістя" if suburb == "all" else suburb
    await cb.message.edit_text(
        f"✅ Локація: <b>🌳 {lbl}</b>\n\nОберіть бюджет:",
        parse_mode="HTML", reply_markup=bkb
    )
    cur_state = await state.get_state()
    if "BuyState" in str(cur_state):
        await state.set_state(BuyState.choosing_budget)
    else:
        await state.set_state(RentState.choosing_budget)


# ══════════════════════════════════════════════════════════════════
#  ВИБІР БЮДЖЕТУ → ПОШУК
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("budget:"))
async def pick_budget(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")[1:]
    pf = int(parts[0]) if len(parts) > 0 else 0
    pt = int(parts[1]) if len(parts) > 1 else 0
    await state.update_data(price_from=pf, price_to=pt)
    await cb.answer()
    data = await state.get_data()
    await do_search(cb.message, data, edit=True)
    await state.clear()


async def do_search(msg: Message, d: dict, edit: bool = False):
    stype    = d.get("search_type","buy_apt")
    rooms    = d.get("rooms","all")
    district = d.get("district","all")
    pf       = d.get("price_from",0)
    pt       = d.get("price_to",0)
    location = d.get("location","lviv")
    suburb   = d.get("suburb","all")

    loading = "Я вже знайшов для Вас пропозиції, тримайте 👇"

    if edit:
        try:
            await msg.edit_text(loading)
        except Exception:
            await msg.answer(loading)
    else:
        await msg.answer(loading)

    await asyncio.sleep(0.8)

    results = filter_objects(search_type=stype, district=district,
                             rooms=rooms, price_from=pf, price_to=pt, location=location)

    if location == "suburbs" and suburb != "all":
        results = [o for o in results if suburb.lower() in
                   (o.get("street","")+" "+o.get("district","")+" "+o.get("raw","")).lower()]

    if not results:
        # Як у AVANGARD — "на жаль зараз немає але ми шукаємо"
        kb = InlineKeyboardBuilder()
        kb.button(text="🔔 Підписатись — сповіщу як з'явиться", callback_data="act:subscribe")
        kb.button(text="🔄 Змінити параметри",                   callback_data="back:main")
        kb.adjust(1)
        await msg.answer(
            "На жаль відповідних варіантів зараз немає 😔\n\n"
            "Проте не хвилюйтесь, наша база нерухомості постійно оновлюється, "
            "тому як тільки я знайду щось цікаве — одразу надішлю пропозиції у цей чат! 🙌",
            reply_markup=kb.as_markup()
        )
        # Сповіщаємо ріелтора
        try:
            type_lbl = {"rent_apt":"Оренда кв","buy_apt":"Купівля кв","buy_house":"Будинок","land":"Ділянка"}
            await bot.send_message(
                AGENT_CHAT_ID,
                f"🔍 <b>ПОШУК БЕЗ РЕЗУЛЬТАТУ</b>\n\n"
                f"Тип: {type_lbl.get(stype,stype)}\n"
                f"Кімнат: {rooms}  Район: {district}\n"
                f"Бюджет: {pf:,}–{pt:,}\n"
                f"👤 @{msg.chat.username or '—'} · {msg.chat.id}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    for obj in results[:3]:
        await asyncio.sleep(0.3)
        await send_obj_card(msg, obj)

    if len(results) > 3:
        kb = InlineKeyboardBuilder()
        kb.button(text=f"Показати ще {len(results)-3} об'єктів", callback_data="action:show_all")
        kb.button(text="🔔 Підписатись на нові",                  callback_data="act:subscribe")
        kb.button(text="🔄 Змінити параметри",                    callback_data="back:main")
        kb.adjust(1)
        await msg.answer(f"Знайдено <b>{len(results)}</b> об'єктів 🏠",
                        parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await msg.answer(
            "Це всі актуальні пропозиції зараз 😊\n"
            "Хочете щоб я сповіщав про нові об'єкти?",
            reply_markup=after_search_kb()
        )


# ══════════════════════════════════════════════════════════════════
#  ПЕРЕГЛЯД / ГАЛЕРЕЯ
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("view:"))
async def cb_view(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":")[1]
    obj  = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    await cb.answer()
    title = (obj.get("title","")[:40] + "...") if obj and len(obj.get("title","")) > 40 else (obj.get("title","") if obj else f"#{code}")

    await cb.message.answer(
        f"📅 <b>Записатись на перегляд</b>\n\n"
        f"🏠 {title}\n\n"
        f"Оберіть зручний час:\n\n"
        f"<b>Завтра</b> — 10:00  12:00  14:00  16:00  18:00\n"
        f"<b>Пн–Пт</b> — 9:00 до 20:00\n\n"
        f"Введіть зручний час або зателефонуйте:\n"
        f"📱 <b>{AGENT_PHONE}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Підтвердити запис",    callback_data=f"confirm_view:{code}")],
            [InlineKeyboardButton(text="📞 Зателефонувати зараз", callback_data="act:contact")],
            [InlineKeyboardButton(text="◀ Назад",                 callback_data="back:main")],
        ])
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
        f"✅ <b>Ви записані на перегляд!</b>\n\n"
        f"Ріелтор зателефонує Вам для підтвердження часу.\n\n"
        f"📱 {AGENT_PHONE}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Головне меню", callback_data="back:main")
        ]])
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"✅ <b>ПІДТВЕРДЖЕННЯ ПЕРЕГЛЯДУ</b>\n🆔 #{code}\n"
            f"👤 @{cb.from_user.username or '—'} · {cb.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith("gallery:"))
async def cb_gallery(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    obj  = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    await cb.answer()
    if obj:
        await send_gallery(cb.message, obj)
    else:
        await cb.message.answer("Об'єкт не знайдено в базі.")

@dp.callback_query(F.data.startswith("save:"))
async def cb_save(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    await cb.answer("💾 Збережено!")
    # У майбутньому — зберігати в БД
    obj = next((o for o in OBJECTS_DB if str(o.get("code")) == code), None)
    if obj:
        try:
            await bot.send_message(
                AGENT_CHAT_ID,
                f"💾 <b>ОБ'ЄКТ ЗБЕРЕЖЕНИЙ</b>\n🆔 #{code}\n"
                f"👤 @{cb.from_user.username or '—'} · {cb.from_user.id}\n"
                f"💰 {fmt_price(obj.get('price_usd',0), obj.get('search_type','buy_apt'))}",
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
    await cb.message.edit_text("📍 Оберіть напрямок:", reply_markup=district_kb())
    await state.set_state(BuyState.choosing_district)

@dp.callback_query(F.data == "land:sell")
async def land_sell(cb: CallbackQuery, state: FSMContext):
    await state.update_data(sell_type="land_sell")
    await state.set_state(SellState.entering_address)
    await cb.answer()
    await cb.message.edit_text(
        "🌿 <b>Продати ділянку</b>\n\n✅ Безкоштовна оцінка\n✅ Перевірка документів\n\n📍 Адреса/напрямок:",
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
    p = {
        "land_sell":  "📐 Площа та комунікації:",
        "sell_house": "📐 Площа будинку, поверхів, ділянка:",
    }.get(stype, "📐 Площа, поверх, рік:")
    await msg.answer(p)
    await state.set_state(SellState.entering_details)

@dp.message(SellState.entering_details)
async def sell_det(msg: Message, state: FSMContext):
    await state.update_data(details=msg.text)
    await msg.answer("💰 Бажана ціна (або «оцінка» — оцінимо безкоштовно):")
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
             "land_sell":"Продаж ділянки","give_apt":"Здача в оренду"}
    label = lbls.get(data.get("sell_type",""), "Заявка")
    await msg.answer(
        f"✅ <b>Заявку отримано!</b>\n\n"
        f"📋 {label}\n📍 {data.get('address','—')}\n"
        f"📐 {data.get('details','—')}\n💰 {data.get('price','—')}\n"
        f"📱 {data.get('phone','—')}\n\n"
        f"⏰ Ріелтор зателефонує протягом <b>15 хвилин</b>!",
        parse_mode="HTML", reply_markup=main_menu_kb()
    )
    # CRM + Telegram ріелтору
    crm_data = {
        "name": msg.from_user.full_name,
        "phone": data.get("phone",""),
        "type": label,
        "address": data.get("address",""),
        "budget": data.get("price",""),
        "comment": f"{data.get('details','')} | @{msg.from_user.username or msg.from_user.id}",
    }
    await send_to_crm(crm_data)
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
#  КОНТАКТ
# ══════════════════════════════════════════════════════════════════
@dp.message(ContactState.entering_phone)
async def contact_phone(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    await msg.answer("💬 Коротко опишіть що вас цікавить:")
    await state.set_state(ContactState.entering_comment)

@dp.message(ContactState.entering_comment)
async def contact_comment(msg: Message, state: FSMContext):
    await state.update_data(comment=msg.text)
    data = await state.get_data()
    await msg.answer(
        f"✅ <b>Дякую!</b>\n\n"
        f"Ріелтор зателефонує на <b>{data.get('phone','')}</b> за 15 хвилин.\n\n"
        f"Або самі: 📱 <b>{AGENT_PHONE}</b>",
        parse_mode="HTML", reply_markup=main_menu_kb()
    )
    crm_data = {
        "name": msg.from_user.full_name,
        "phone": data.get("phone",""),
        "comment": data.get("comment",""),
        "type": "Запит на зв'язок",
    }
    await send_to_crm(crm_data)
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"📞 <b>ЗАПИТ НА ЗВ'ЯЗОК</b>\n\n"
            f"👤 {msg.from_user.full_name}\n📱 {data.get('phone','—')}\n"
            f"💬 {data.get('comment','—')}\n"
            f"🔗 @{msg.from_user.username or '—'} · {msg.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()


# ══════════════════════════════════════════════════════════════════
#  ПІДПИСКА НА СПОВІЩЕННЯ
# ══════════════════════════════════════════════════════════════════
class SubState(StatesGroup):
    choosing_type     = State()
    choosing_district = State()
    choosing_budget   = State()
    choosing_freq     = State()

async def start_subscribe(msg, state: FSMContext, edit: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text="Орендувати квартиру", callback_data="sub_type:rent_apt")
    kb.button(text="Купити квартиру",     callback_data="sub_type:buy_apt")
    kb.button(text="Купити будинок",      callback_data="sub_type:buy_house")
    kb.button(text="Земельна ділянка",    callback_data="sub_type:land")
    kb.button(text="◀ Назад",            callback_data="back:main")
    kb.adjust(2, 2, 1)
    t = (
        "🔔 <b>Налаштування сповіщень</b>\n\n"
        "Я буду першим надсилати нові об'єкти та зниження цін!\n\n"
        "Що вас цікавить?"
    )
    if edit:
        try:
            await msg.edit_text(t, parse_mode="HTML", reply_markup=kb.as_markup())
        except Exception:
            await msg.answer(t, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await msg.answer(t, parse_mode="HTML", reply_markup=kb.as_markup())
    await state.set_state(SubState.choosing_type)

@dp.callback_query(SubState.choosing_type, F.data.startswith("sub_type:"))
async def sub_type(cb: CallbackQuery, state: FSMContext):
    st  = cb.data.split(":")[1]
    cur = "₴" if st == "rent_apt" else "$"
    await state.update_data(sub_type=st, currency=cur)
    await cb.answer()
    await cb.message.edit_text("📍 Оберіть район:", reply_markup=district_kb())
    await state.set_state(SubState.choosing_district)

@dp.callback_query(SubState.choosing_district, F.data.startswith("district:"))
async def sub_district(cb: CallbackQuery, state: FSMContext):
    d = cb.data.split(":")[1]
    await state.update_data(sub_district=d, sub_location="lviv")
    await cb.answer()
    data = await state.get_data()
    bkb  = budget_rent_kb() if data.get("currency") == "₴" else budget_buy_kb()
    lbl  = "Всі райони" if d == "all" else d
    await cb.message.edit_text(
        f"✅ Район: <b>{lbl}</b>\n\nОберіть бюджет:",
        parse_mode="HTML", reply_markup=bkb
    )
    await state.set_state(SubState.choosing_budget)

@dp.callback_query(SubState.choosing_budget, F.data.startswith("budget:"))
async def sub_budget(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")[1:]
    pf = int(parts[0]) if parts else 0
    pt = int(parts[1]) if len(parts) > 1 else 0
    await state.update_data(sub_price_from=pf, sub_price_to=pt)
    await cb.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="⚡ Одразу як з'явиться",  callback_data="freq:instant")
    kb.button(text="📅 Раз на день",           callback_data="freq:daily")
    kb.button(text="📆 Раз на тиждень",        callback_data="freq:weekly")
    kb.adjust(1)
    await cb.message.edit_text("⏰ Як часто надсилати сповіщення?", reply_markup=kb.as_markup())
    await state.set_state(SubState.choosing_freq)

@dp.callback_query(SubState.choosing_freq, F.data.startswith("freq:"))
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
    fl = {"instant":"одразу ⚡","daily":"раз на день 📅","weekly":"раз на тиждень 📆"}
    await cb.answer()
    await cb.message.edit_text(
        f"✅ <b>Ваша підписка активна!</b>\n\n"
        f"🏠 {data.get('sub_type','all')}\n"
        f"📍 {'Всі райони' if data.get('sub_district')=='all' else data.get('sub_district')}\n"
        f"⏰ {fl.get(freq)}\n\n"
        f"Я буду першим надсилати нові об'єкти! 🙌",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Головне меню", callback_data="back:main")
        ]])
    )
    try:
        await bot.send_message(
            AGENT_CHAT_ID,
            f"🔔 <b>НОВИЙ ПІДПИСНИК</b>\n"
            f"👤 @{cb.from_user.username or '—'} · {uid}\n"
            f"🏠 {data.get('sub_type')} · {data.get('sub_district')}\n"
            f"💰 {data.get('sub_price_from',0):,}–{data.get('sub_price_to',0):,} · {fl.get(freq)}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()

@dp.callback_query(F.data == "sub:view")
async def sub_view(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid not in subscriptions:
        await cb.answer("Підписок немає"); return
    s = subscriptions[uid]
    fl= {"instant":"одразу","daily":"раз на день","weekly":"раз на тиждень"}
    await cb.answer()
    await cb.message.answer(
        f"🔔 <b>Ваша підписка:</b>\n\n"
        f"🏠 {s.get('type')}\n📍 {s.get('district','всі')}\n"
        f"💰 {s.get('price_from',0):,}–{s.get('price_to',0):,} {s.get('currency','$')}\n"
        f"⏰ {fl.get(s.get('frequency','instant'))}",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "sub:pause")
async def sub_pause(cb: CallbackQuery):
    subscriptions.pop(cb.from_user.id, None)
    await cb.answer("Вимкнено")
    await cb.message.edit_text(
        "🔕 Підписку призупинено.\n\nМожна увімкнути знову будь-коли.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Увімкнути знову", callback_data="act:subscribe"),
             InlineKeyboardButton(text="🏠 Меню",            callback_data="back:main")],
        ])
    )


# ══════════════════════════════════════════════════════════════════
#  ADD_OBJECT (менеджер)
# ══════════════════════════════════════════════════════════════════
@dp.message(Command("add_object"))
async def cmd_add(msg: Message, state: FSMContext):
    if str(msg.from_user.id) != str(AGENT_CHAT_ID):
        return
    await state.set_state(AddObjectState.waiting_data)
    await msg.answer("📋 Вставте рядок з RealtSoft CRM:")

@dp.message(AddObjectState.waiting_data)
async def receive_crm(msg: Message, state: FSMContext):
    obj = parse_crm_line(msg.text)
    if not obj:
        await msg.answer("⚠️ Не вдалось розпізнати формат."); return
    codes = [o.get("code") for o in OBJECTS_DB]
    if obj["code"] in codes:
        OBJECTS_DB[:] = [obj if o["code"]==obj["code"] else o for o in OBJECTS_DB]
        action = "оновлено ♻️"
    else:
        OBJECTS_DB.append(obj)
        action = "додано ✅"
    await state.clear()
    await msg.answer(
        f"Об'єкт <b>{action}</b>\n"
        f"🆔 #{obj['code']}  📍 {obj['district']}\n"
        f"💰 {fmt_price(obj.get('price_usd',0), obj.get('search_type','buy_apt'))}"
        f"  📸 {len(obj.get('photos',[]))} фото\n\nРозіслати підписникам?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Так, розіслати", callback_data=f"notify:{obj['code']}"),
             InlineKeyboardButton(text="⏭ Пропустити",     callback_data="back:main")],
        ])
    )

@dp.callback_query(F.data.startswith("notify:"))
async def do_notify(cb: CallbackQuery):
    code = cb.data.split(":")[1]
    obj  = next((o for o in OBJECTS_DB if str(o.get("code"))==code), None)
    if not obj:
        await cb.answer("Не знайдено"); return
    await cb.answer("Розсилаю...")
    n = await broadcast_new_object(obj)
    await cb.message.answer(f"✅ Надіслано <b>{n}</b> підписникам!", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════
#  РОЗСИЛКА
# ══════════════════════════════════════════════════════════════════
async def broadcast_new_object(obj: dict) -> int:
    sent = 0
    for uid, sub in subscriptions.items():
        if sub["type"] != "all" and sub["type"] != obj.get("search_type"):
            continue
        if sub["district"] != "all":
            if sub["district"].lower() not in obj.get("district","").lower():
                continue
        p = obj.get("price_usd", 0)
        if sub["price_from"] > 0 and p < sub["price_from"]: continue
        if sub["price_to"]   > 0 and p > sub["price_to"]:   continue
        kb = InlineKeyboardBuilder()
        kb.button(text="📅 Записатись на перегляд", callback_data=f"view:{obj.get('code','')}")
        kb.button(text="💾 Зберегти",               callback_data=f"save:{obj.get('code','')}")
        kb.adjust(2)
        cap  = f"✨ <b>Новий об'єкт за Вашим запитом!</b>\n\n" + card_caption(obj, full=False)
        try:
            photos = obj.get("photos",[])
            if photos:
                await bot.send_photo(uid, photo=photos[0], caption=cap,
                                     parse_mode="HTML", reply_markup=kb.as_markup())
            else:
                await bot.send_message(uid, cap, parse_mode="HTML", reply_markup=kb.as_markup())
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
    await msg.answer("Оберіть дію 👇", reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════
async def main():
    load_objects("objects.txt")
    print(f"🤖 {AGENCY_NAME} Bot запущено. CRM webhook: {'✅' if CRM_WEBHOOK_URL else '❌ не налаштований'}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
