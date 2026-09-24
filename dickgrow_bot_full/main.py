import os, sqlite3, random, time, re, logging, traceback
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("man_market_bot")

TOKEN = os.getenv("BOT_TOKEN")
DB = os.getenv("DB_PATH", "database.db")
COOLDOWN=1*60*60
ADMIN_IDS = {5952134460, 5429762495}

db_dir = os.path.dirname(DB)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
db=sqlite3.connect(DB)
c=db.cursor()

# ===== همه‌ی جدول‌های اقتصادی حالا با chat_id اسکوپ میشن =====
# یعنی هر گروه (یا چت خصوصی) یه اقتصاد کاملاً مستقل داره؛
# آیدی عددی کاربر تو گروه‌های مختلف به‌صورت جدا شمرده میشه.
c.execute("""CREATE TABLE IF NOT EXISTS users(
    chat_id INTEGER,
    user_id INTEGER,
    name TEXT,
    size INTEGER DEFAULT 0,
    sperm INTEGER DEFAULT 0,
    debt INTEGER DEFAULT 0,
    last_grow INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)""")
c.execute("CREATE TABLE IF NOT EXISTS battles(id INTEGER PRIMARY KEY AUTOINCREMENT,creator INTEGER,bet INTEGER,active INTEGER DEFAULT 1)")
c.execute("""CREATE TABLE IF NOT EXISTS pvp_stats(
    chat_id INTEGER,
    user_id INTEGER,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS rank_stats(
    chat_id INTEGER,
    user_id INTEGER,
    rp INTEGER DEFAULT 0,
    duels_today INTEGER DEFAULT 0,
    last_active_day TEXT DEFAULT '',
    PRIMARY KEY(chat_id, user_id)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS loans(
    chat_id INTEGER,
    lender_id INTEGER,
    borrower_id INTEGER,
    amount INTEGER,
    loan_time INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS listings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    seller_id INTEGER,
    celeb TEXT,
    price INTEGER,
    active INTEGER DEFAULT 1
)""")
c.execute("""CREATE TABLE IF NOT EXISTS game_loans(
    chat_id INTEGER,
    user_id INTEGER,
    amount INTEGER,
    due_time INTEGER,
    PRIMARY KEY(chat_id, user_id)
)""")
db.commit()

try:
    c.execute("ALTER TABLE users ADD COLUMN sperm INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass

try:
    c.execute("ALTER TABLE users ADD COLUMN farm_plots INTEGER DEFAULT 2")
    db.commit()
except sqlite3.OperationalError:
    pass

c.execute("""CREATE TABLE IF NOT EXISTS farm_tiles(
    chat_id INTEGER,
    user_id INTEGER,
    plot_num INTEGER,
    egg_type TEXT,
    planted_at INTEGER,
    PRIMARY KEY(chat_id, user_id, plot_num)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS farm_inventory(
    chat_id INTEGER,
    user_id INTEGER,
    egg_type TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id, egg_type)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS farm_records(
    chat_id INTEGER,
    user_id INTEGER,
    eggs_bought INTEGER DEFAULT 0,
    eggs_planted INTEGER DEFAULT 0,
    eggs_harvested INTEGER DEFAULT 0,
    total_harvest_value INTEGER DEFAULT 0,
    plots_opened INTEGER DEFAULT 0,
    spent_cm INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)""")
db.commit()

SPERM_RATE = 2  # ۱ سانت = ۲ اسپرم (واحد جداگانه‌ی بخش بورس)

def strip_command(text: str) -> str:
    """حذف /command یا /command@botusername از ابتدای متن و برگردوندن بقیه‌ی متن.
    این کار لازمه چون تو گروه‌ها تلگرام معمولاً @یوزرنیم بات رو به دستور می‌چسبونه
    (مثلاً /buy@YourBot Kylie Jenner) و replace ساده‌ی رشته این حالت رو درست پارس نمی‌کنه."""
    return re.sub(r'^/\S+\s*', '', text or '', count=1).strip()

def user(chat_id,uid,name):
    c.execute("INSERT OR IGNORE INTO users(chat_id,user_id,name) VALUES(?,?,?)",(chat_id,uid,name))
    c.execute("UPDATE users SET name=? WHERE chat_id=? AND user_id=?",(name,chat_id,uid))
    db.commit()

def get_size(chat_id,uid):
    row=c.execute("SELECT size FROM users WHERE chat_id=? AND user_id=?",(chat_id,uid)).fetchone()
    return row[0] if row else 0

def get_sperm(chat_id,uid):
    row=c.execute("SELECT sperm FROM users WHERE chat_id=? AND user_id=?",(chat_id,uid)).fetchone()
    return row[0] if row else 0

def get_name(chat_id,uid):
    row=c.execute("SELECT name FROM users WHERE chat_id=? AND user_id=?",(chat_id,uid)).fetchone()
    return row[0] if row else "کاربر"

dp=Dispatcher()

# ===== راهنمای بازی =====
HELP_SECTIONS = {
    "econ": (
        "💰 اقتصاد پایه",
        "🍆 /grow — هر چندساعت یه‌بار سایزت رو رشد بده\n"
        "📊 /size — دیدن اندازه، اسپرم و بدهیت\n"
        "🏆 /top — لیدربرد بزرگ‌ترین‌های گروه\n"
        "💳 /loan — گرفتن وام (با بهره)\n"
        "💵 /repay [مقدار] — پرداخت وام\n"
        "🧬 /tosperm [مقدار سانت] — تبدیل سانت به اسپرم (نرخ ۱ به ۲)\n"
        "💰 /tocent [مقدار اسپرم] — تبدیل اسپرم به سانت"
    ),
    "pvp": (
        "⚔️ دوئل و رنک",
        "⚔️ /pvp [مبلغ شرط] — چالش دادن به یه نفر برای دوئل ۱ به ۱\n"
        "🏅 /rank — دیدن رنک، امتیاز (RP) و پیشرفت روزانه‌ت\n"
        "🏆 /ranktop — لیدربرد رنک گروه\n"
        "\nنکته: شانس بردت تو دوئل ثابت نیست — اگه زیادی برده باشی شانست کم می‌شه، اگه زیادی باخته باشی شانست زیاد می‌شه تا عادلانه بمونه."
    ),
    "mafia": (
        "🔫 مافیا",
        "🔫 /mafia [مبلغ شرط] — ساختن یه نبرد مافیای تیمی (بدون محدودیت نفرات)\n"
        "🕵️ /mafia2 [مبلغ شرط] — نسخه‌ی مخفیانه‌ی مافیا (تیم‌ها لو نمی‌رن)\n\n"
        "بقیه با زدن دکمه‌ی داخل پیام به یه تیم ملحق می‌شن، بعد سازنده یا حریف با «شروع نبرد» بازی رو تموم می‌کنه. تیم بزرگ‌تر می‌بره و جایزه بین برنده‌ها تقسیم می‌شه."
    ),
    "celeb": (
        "🌟 سلبریتی‌ها",
        "🎰 /spin [تیر: S/A/B/PH] — اسپین گرفتن یه سلبریتی رندوم از یه تیر\n"
        "🏪 /market — دیدن سلبریتی‌های قابل‌خرید\n"
        "🛒 /buy [اسم] — خرید مستقیم یه سلبریتی از مارکت\n"
        "📁 /collection — دیدن کلکسیون خودت\n"
        "📜 /list — لیست کردن یه سلبریتی برای فروش به بقیه\n"
        "💸 /sell [اسم] — فروش یه سلبریتی\n"
        "🔒 /lock [اسم] — قفل کردن یه سلبریتی در برابر فروش خودکار (هزینه بر اساس تیرش فرق داره)\n"
        "🏅 /collectors — کی بیشترین سلبریتی رو داره\n"
        "💳 /gloan — وام مخصوص خرید سلبریتی\n"
        "💵 /gpay [مقدار] — پرداخت اون وام"
    ),
    "bourse": (
        "📈 بورس",
        "📈 /copen — باز کردن یه دور بورس (ادمین)\n"
        "🧬 /invest — سرمایه‌گذاری مخفیانه روی یه شرکت (با اسپرم، از طریق پیوی)\n"
        "📉 /cclose — بستن بورس و اعلام برنده‌ها (ادمین)\n"
        "🏢 /mycompanies — شرکت‌هایی که بردی\n"
        "📊 /bourseleader — جدول رنک بورس گروه (ارزش خالص، تعداد شرکت، بردها، سود روزانه)\n"
        "🕵️ /useyar — فرستادن یه یار (کارگر) از شرکتت به یه نبرد مافیای فعلیت\n"
        "💰 /cashout [شماره شرکت] — نقد کردن نصف ارزش یه شرکت به اسپرم\n\n"
        "بورس با «اسپرم» کار می‌کنه، نه سانت — اول با /tosperm سانتت رو تبدیل کن."
    ),
    "farm": (
        "🌾 مزرعه‌ی اسپرم",
        "🌾 /farm — باز کردن پنل مزرعه (یه گرید از زمین‌هات با دکمه)\n\n"
        "➕ زمین خالی → بزن روش تا تخمی که تو انبارت داری رو بکاری\n"
        "⏳ در حال رشد → بزن روش تا ببینی چقدر مونده\n"
        "✅ آماده‌ی برداشت → بزن روش تا اسپرم بگیری\n"
        "💀 فاسدشده → اگه دیر بجنبی نصف ارزشش رو می‌گیری\n\n"
        "از همون پنل می‌تونی زمین جدید باز کنی، یه تخم تکی شانسی بخری (۵ سانت)، یا یه پک ۳تایی بخری (۳۰ سانت). رنگ‌های ارزون (⚪🟢) شانس دارن موقع برداشت یه تخم دیگه از خودشون بندازن؛ رنگ‌های کمیاب (🔵🟣🟡) هیچ‌وقت تکرار نمی‌شن ولی سودشون خیلی بیشتره.\n\n"
        "🌾 /farmrank — رنک مزرعه‌ی خودت، بر اساس دقیقاً چیزی که الان تو زمین‌ها و انبارته\n"
        "📊 /farmleader — جدول رنک مزرعه‌ی کل گروه"
    ),
}

@dp.message(Command("help"))
async def help_cmd(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 اقتصاد پایه", callback_data="help:econ")],
        [InlineKeyboardButton(text="⚔️ دوئل و رنک", callback_data="help:pvp")],
        [InlineKeyboardButton(text="🔫 مافیا", callback_data="help:mafia")],
        [InlineKeyboardButton(text="🌟 سلبریتی‌ها", callback_data="help:celeb")],
        [InlineKeyboardButton(text="📈 بورس", callback_data="help:bourse")],
        [InlineKeyboardButton(text="🌾 مزرعه", callback_data="help:farm")],
    ])
    await m.reply(
        "📖 راهنمای بازی\n\n"
        "یه بخش رو انتخاب کن تا کامندهاش رو ببینی:",
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("help:"))
async def help_section(q: CallbackQuery):
    key = q.data.split(":")[1]
    main_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 اقتصاد پایه", callback_data="help:econ")],
        [InlineKeyboardButton(text="⚔️ دوئل و رنک", callback_data="help:pvp")],
        [InlineKeyboardButton(text="🔫 مافیا", callback_data="help:mafia")],
        [InlineKeyboardButton(text="🌟 سلبریتی‌ها", callback_data="help:celeb")],
        [InlineKeyboardButton(text="📈 بورس", callback_data="help:bourse")],
        [InlineKeyboardButton(text="🌾 مزرعه", callback_data="help:farm")],
    ])
    if key == "back" or key not in HELP_SECTIONS:
        await q.message.edit_text("📖 راهنمای بازی\n\nیه بخش رو انتخاب کن تا کامندهاش رو ببینی:", reply_markup=main_kb)
        return await q.answer()
    title, body = HELP_SECTIONS[key]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ برگشت به فهرست", callback_data="help:back")]
    ])
    await q.message.edit_text(f"{title}\n\n{body}", reply_markup=kb)
    await q.answer()

# نگهداری موقت سرمایه‌گذاری‌هایی که کاربر توی گروه دکمه‌ش رو زده ولی هنوز مبلغ رو توی پیوی نفرستاده
pending_invest = {}  # user_id -> {"chat_id":..., "round_id":..., "slot":..., "ts":...}
PENDING_INVEST_TTL = 10 * 60  # بعد از ۱۰ دقیقه منقضی می‌شه

@dp.message(Command("grow"))
async def grow(m:Message):
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    size,last=c.execute("SELECT size,last_grow FROM users WHERE chat_id=? AND user_id=?",(m.chat.id,m.from_user.id)).fetchone()
    now=int(time.time())
    if now-last<COOLDOWN:
        rem=(COOLDOWN-(now-last))//60
        return await m.reply(f"⏳ هنوز {rem} دقیقه تا رشد بعدی مونده!")
    delta=random.randint(5,20)
    size=max(0,size+delta)
    c.execute("UPDATE users SET size=?,last_grow=? WHERE chat_id=? AND user_id=?",(size,now,m.chat.id,m.from_user.id)); db.commit()
    await m.reply(
        f"🌱 نتیجه رشد\n\n🍆 تغییر: {delta:+} سانت\n📏 اندازه فعلی: {size} سانت\n😎 ادامه بده قهرمان!"
    )

@dp.message(Command("size"))
async def size(m:Message):
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    s,sp,d=c.execute("SELECT size,sperm,debt FROM users WHERE chat_id=? AND user_id=?",(m.chat.id,m.from_user.id)).fetchone()
    await m.reply(
        f"📊 پروفایل شما\n\n🍆 اندازه: {s} سانت\n🧬 اسپرم (بورس): {sp}\n💸 بدهی: {d} سانت"
    )

@dp.message(Command("tosperm"))
async def to_sperm(m:Message):
    try:
        amount=int(m.text.split()[1])
    except:
        return await m.reply(f"استفاده: /tosperm [مقدار سانت]\nنرخ: هر ۱ سانت = {SPERM_RATE} اسپرم")
    if amount<=0:
        return await m.reply("❌ مقدار باید مثبت باشه.")
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    if get_size(m.chat.id,m.from_user.id)<amount:
        return await m.reply("❌ سانت کافی نداری.")
    gained=amount*SPERM_RATE
    c.execute("UPDATE users SET size=size-?,sperm=sperm+? WHERE chat_id=? AND user_id=?",(amount,gained,m.chat.id,m.from_user.id))
    db.commit()
    await m.reply(f"🧬 {amount} سانت تبدیل شد به {gained} اسپرم!")

@dp.message(Command("tocent"))
async def to_cent(m:Message):
    try:
        amount=int(m.text.split()[1])
    except:
        return await m.reply(f"استفاده: /tocent [مقدار اسپرم]\nنرخ: هر {SPERM_RATE} اسپرم = ۱ سانت")
    if amount<=0:
        return await m.reply("❌ مقدار باید مثبت باشه.")
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    if get_sperm(m.chat.id,m.from_user.id)<amount:
        return await m.reply("❌ اسپرم کافی نداری.")
    gained=amount//SPERM_RATE
    if gained<=0:
        return await m.reply(f"❌ حداقل {SPERM_RATE} اسپرم لازمه تا ۱ سانت بگیری.")
    used=gained*SPERM_RATE
    c.execute("UPDATE users SET sperm=sperm-?,size=size+? WHERE chat_id=? AND user_id=?",(used,gained,m.chat.id,m.from_user.id))
    db.commit()
    await m.reply(f"💰 {used} اسپرم تبدیل شد به {gained} سانت!")

# ===== مزرعه‌ی اسپرم =====
EGGS = {
    "white":  {"emoji": "⚪", "name": "تخم شل",         "chance": 50, "time": 30*60,    "min": 5,   "max": 10,  "replicate": 30},
    "green":  {"emoji": "🟢", "name": "تخم آبدار",      "chance": 25, "time": 2*3600,   "min": 20,  "max": 35,  "replicate": 15},
    "blue":   {"emoji": "🔵", "name": "تخم پرقدرت",     "chance": 15, "time": 6*3600,   "min": 60,  "max": 100, "replicate": 0},
    "purple": {"emoji": "🟣", "name": "تخم رعدآسا",     "chance": 8,  "time": 12*3600,  "min": 150, "max": 250, "replicate": 0},
    "gold":   {"emoji": "🟡", "name": "تخم ایلان ماسک", "chance": 2,  "time": 24*3600,  "min": 500, "max": 800, "replicate": 0},
}
EGG_SURPRISE_CHANCE = 0.02   # شانس اینکه یه تخم ارزون در واقع طلایی مخفی دربیاد
FARM_SPOIL_MULTIPLIER = 2    # بعد این‌قدر برابر زمان اصلی، تخم فاسد میشه و نصف ارزش میده
FARM_PAGE_SIZE = 9           # هر صفحه از پنل چندتا خونه نشون بده (۳ در ۳)
EGG_SINGLE_COST = 5          # هزینه‌ی یه تخم تکی شانسی
EGG_PACK_COST = 30           # هزینه‌ی هر پک (۳ تخم شانسی)
EGG_PACK_SIZE = 3
PLOT_BASE_COST = 20          # هزینه‌ی باز کردن اولین زمین‌های اضافه؛ هرچی بیشتر داشته باشی گرون‌تر میشه

def roll_egg():
    names = list(EGGS.keys())
    weights = [EGGS[n]["chance"] for n in names]
    egg = random.choices(names, weights=weights, k=1)[0]
    if egg in ("white", "green") and random.random() < EGG_SURPRISE_CHANCE:
        return "gold", True
    return egg, False

def add_egg_to_inventory(chat_id, uid, egg_type, qty=1):
    c.execute(
        "INSERT INTO farm_inventory(chat_id,user_id,egg_type,count) VALUES(?,?,?,?) "
        "ON CONFLICT(chat_id,user_id,egg_type) DO UPDATE SET count=count+excluded.count",
        (chat_id, uid, egg_type, qty)
    )

def get_inventory(chat_id, uid):
    rows = c.execute("SELECT egg_type,count FROM farm_inventory WHERE chat_id=? AND user_id=? AND count>0", (chat_id, uid)).fetchall()
    return {t: n for t, n in rows}

def bump_farm_record(chat_id, uid, **deltas):
    cols = ("eggs_bought", "eggs_planted", "eggs_harvested", "total_harvest_value", "plots_opened", "spent_cm")
    c.execute(
        f"INSERT INTO farm_records(chat_id,user_id,{','.join(cols)}) VALUES(?,?,{','.join(['0']*len(cols))}) "
        "ON CONFLICT(chat_id,user_id) DO NOTHING",
        (chat_id, uid)
    )
    for col, val in deltas.items():
        if val:
            c.execute(f"UPDATE farm_records SET {col}={col}+? WHERE chat_id=? AND user_id=?", (val, chat_id, uid))

def get_farm_record(chat_id, uid):
    row = c.execute(
        "SELECT eggs_bought,eggs_planted,eggs_harvested,total_harvest_value,plots_opened,spent_cm "
        "FROM farm_records WHERE chat_id=? AND user_id=?", (chat_id, uid)
    ).fetchone()
    if not row:
        return {"eggs_bought": 0, "eggs_planted": 0, "eggs_harvested": 0, "total_harvest_value": 0, "plots_opened": 0, "spent_cm": 0}
    keys = ("eggs_bought", "eggs_planted", "eggs_harvested", "total_harvest_value", "plots_opened", "spent_cm")
    return dict(zip(keys, row))

def plot_unlock_cost(owned):
    return PLOT_BASE_COST + 15 * max(0, owned - 2)

def tile_state(egg_type, planted_at):
    """برمی‌گردونه: ('growing'|'ready'|'spoiled', ثانیه‌ی باقی‌مونده یا گذشته)"""
    info = EGGS[egg_type]
    elapsed = int(time.time()) - planted_at
    if elapsed < info["time"]:
        return "growing", info["time"] - elapsed
    if elapsed < info["time"] * FARM_SPOIL_MULTIPLIER:
        return "ready", elapsed - info["time"]
    return "spoiled", elapsed - info["time"] * FARM_SPOIL_MULTIPLIER

def fmt_time(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    mnt, _ = divmod(rem, 60)
    if h:
        return f"{h}:{mnt:02d}h"
    return f"{mnt}m"

def build_farm_keyboard(chat_id, uid, page):
    owned = c.execute("SELECT farm_plots FROM users WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()
    owned = owned[0] if owned else 2
    tiles = {n: (e, p) for n, e, p in c.execute(
        "SELECT plot_num,egg_type,planted_at FROM farm_tiles WHERE chat_id=? AND user_id=?", (chat_id, uid)
    ).fetchall()}
    total_pages = max(1, (owned + FARM_PAGE_SIZE - 1) // FARM_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * FARM_PAGE_SIZE
    rows = []
    row = []
    for i in range(start, start + FARM_PAGE_SIZE):
        if i >= owned:
            row.append(InlineKeyboardButton(text="🔒", callback_data="farm:noop"))
        elif i in tiles:
            egg_type, planted_at = tiles[i]
            state, _ = tile_state(egg_type, planted_at)
            emoji = EGGS[egg_type]["emoji"]
            label = {"growing": f"{emoji}⏳", "ready": f"✅{emoji}", "spoiled": f"💀{emoji}"}[state]
            row.append(InlineKeyboardButton(text=label, callback_data=f"farm:tile:{page}:{i}"))
        else:
            row.append(InlineKeyboardButton(text="➕", callback_data=f"farm:tile:{page}:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"farm:page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="farm:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"farm:page:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text=f"➕ باز کردن زمین جدید ({plot_unlock_cost(owned)} سانت)", callback_data=f"farm:unlock:{page}")])
    rows.append([InlineKeyboardButton(text=f"🥚 یه تخم تکی ({EGG_SINGLE_COST} سانت)", callback_data=f"farm:buyegg:{page}")])
    rows.append([InlineKeyboardButton(text=f"📦 پک ۳تایی ({EGG_PACK_COST} سانت)", callback_data=f"farm:buypack:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_farm_text(chat_id, uid):
    inv = get_inventory(chat_id, uid)
    inv_txt = "، ".join(f"{EGGS[t]['emoji']}×{n}" for t, n in inv.items()) if inv else "خالیه"
    return f"🌾 مزرعه‌ی اسپرم\n\n📦 انبار تخم: {inv_txt}\n\n➕ زمین خالی | ⏳ در حال رشد | ✅ آماده‌ی برداشت | 💀 فاسدشده (نصف ارزش)"

@dp.message(Command("farm"))
async def farm_cmd(m: Message):
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    await m.reply(build_farm_text(m.chat.id, m.from_user.id), reply_markup=build_farm_keyboard(m.chat.id, m.from_user.id, 0))

@dp.callback_query(F.data == "farm:noop")
async def farm_noop(q: CallbackQuery):
    await q.answer()

@dp.callback_query(F.data.startswith("farm:page:"))
async def farm_page(q: CallbackQuery):
    page = int(q.data.split(":")[2])
    await q.message.edit_text(build_farm_text(q.message.chat.id, q.from_user.id), reply_markup=build_farm_keyboard(q.message.chat.id, q.from_user.id, page))
    await q.answer()

@dp.callback_query(F.data.startswith("farm:unlock:"))
async def farm_unlock(q: CallbackQuery):
    page = int(q.data.split(":")[2])
    chat_id, uid = q.message.chat.id, q.from_user.id
    user(chat_id, uid, q.from_user.full_name)
    owned = c.execute("SELECT farm_plots FROM users WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()[0]
    cost = plot_unlock_cost(owned)
    if get_size(chat_id, uid) < cost:
        return await q.answer(f"❌ {cost} سانت لازم داری!", show_alert=True)
    c.execute("UPDATE users SET size=size-?, farm_plots=farm_plots+1 WHERE chat_id=? AND user_id=?", (cost, chat_id, uid))
    bump_farm_record(chat_id, uid, plots_opened=1, spent_cm=cost)
    db.commit()
    await q.answer("✅ یه زمین جدید باز شد!")
    await q.message.edit_text(build_farm_text(chat_id, uid), reply_markup=build_farm_keyboard(chat_id, uid, page))

@dp.callback_query(F.data.startswith("farm:buyegg:"))
async def farm_buyegg(q: CallbackQuery):
    page = int(q.data.split(":")[2])
    chat_id, uid = q.message.chat.id, q.from_user.id
    user(chat_id, uid, q.from_user.full_name)
    if get_size(chat_id, uid) < EGG_SINGLE_COST:
        return await q.answer(f"❌ {EGG_SINGLE_COST} سانت لازم داری!", show_alert=True)
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (EGG_SINGLE_COST, chat_id, uid))
    egg, surprise = roll_egg()
    add_egg_to_inventory(chat_id, uid, egg, 1)
    bump_farm_record(chat_id, uid, eggs_bought=1, spent_cm=EGG_SINGLE_COST)
    db.commit()
    alert = f"🥚 گرفتی: {EGGS[egg]['emoji']} {EGGS[egg]['name']}!"
    if surprise:
        alert = "🎉🎉 چی؟! این یکی اصلاً معمولی نبود!\n\n" + alert
    await q.answer(alert, show_alert=True)
    await q.message.edit_text(build_farm_text(chat_id, uid), reply_markup=build_farm_keyboard(chat_id, uid, page))

@dp.callback_query(F.data.startswith("farm:buypack:"))
async def farm_buypack(q: CallbackQuery):
    page = int(q.data.split(":")[2])
    chat_id, uid = q.message.chat.id, q.from_user.id
    user(chat_id, uid, q.from_user.full_name)
    if get_size(chat_id, uid) < EGG_PACK_COST:
        return await q.answer(f"❌ {EGG_PACK_COST} سانت لازم داری!", show_alert=True)
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (EGG_PACK_COST, chat_id, uid))
    results = []
    surprise = False
    for _ in range(EGG_PACK_SIZE):
        egg, was_surprise = roll_egg()
        add_egg_to_inventory(chat_id, uid, egg, 1)
        results.append(egg)
        surprise = surprise or was_surprise
    bump_farm_record(chat_id, uid, eggs_bought=EGG_PACK_SIZE, spent_cm=EGG_PACK_COST)
    db.commit()
    lines = "\n".join(f"{EGGS[e]['emoji']} {EGGS[e]['name']}" for e in results)
    alert = "🎉 پک باز شد:\n" + lines
    if surprise:
        alert = "🎉🎉 چی؟! یکیشون اصلاً معمولی نبود!\n\n" + alert
    await q.answer(alert, show_alert=True)
    await q.message.edit_text(build_farm_text(chat_id, uid), reply_markup=build_farm_keyboard(chat_id, uid, page))

@dp.callback_query(F.data.startswith("farm:tile:"))
async def farm_tile(q: CallbackQuery):
    _, _, page, num = q.data.split(":")
    page, num = int(page), int(num)
    chat_id, uid = q.message.chat.id, q.from_user.id
    user(chat_id, uid, q.from_user.full_name)
    tile = c.execute("SELECT egg_type,planted_at FROM farm_tiles WHERE chat_id=? AND user_id=? AND plot_num=?", (chat_id, uid, num)).fetchone()
    if tile:
        egg_type, planted_at = tile
        state, delta = tile_state(egg_type, planted_at)
        info = EGGS[egg_type]
        if state == "growing":
            return await q.answer(f"{info['emoji']} {info['name']} — {fmt_time(delta)} مونده", show_alert=True)
        gained = random.randint(info["min"], info["max"])
        if state == "spoiled":
            gained //= 2
        c.execute("UPDATE users SET sperm=sperm+? WHERE chat_id=? AND user_id=?", (gained, chat_id, uid))
        c.execute("DELETE FROM farm_tiles WHERE chat_id=? AND user_id=? AND plot_num=?", (chat_id, uid, num))
        bump_farm_record(chat_id, uid, eggs_harvested=1, total_harvest_value=gained)
        msg = f"{'💀 فاسد شده بود ولی بازم چیزی گیرت اومد!' if state=='spoiled' else '✅ برداشت شد!'}\n{info['emoji']} {info['name']}: +{gained} اسپرم"
        if info["replicate"] and random.randint(1, 100) <= info["replicate"]:
            add_egg_to_inventory(chat_id, uid, egg_type, 1)
            msg += f"\n🍀 یه {info['emoji']} {info['name']} دیگه هم افتاد تو انبارت!"
        db.commit()
        await q.answer(msg, show_alert=True)
        return await q.message.edit_text(build_farm_text(chat_id, uid), reply_markup=build_farm_keyboard(chat_id, uid, page))
    # زمین خالیه — نشون بده چی تو انبار داره که بکاره
    inv = get_inventory(chat_id, uid)
    if not inv:
        return await q.answer("📭 انبارت خالیه! اول یه پک تخم بخر.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EGGS[t]['emoji']} {EGGS[t]['name']} ({n} تا)", callback_data=f"farm:plant:{page}:{num}:{t}")]
        for t, n in inv.items()
    ] + [[InlineKeyboardButton(text="⬅️ برگشت", callback_data=f"farm:page:{page}")]])
    await q.message.edit_text(f"🌱 کدوم تخم رو تو این زمین بکاری؟", reply_markup=kb)
    await q.answer()

@dp.callback_query(F.data.startswith("farm:plant:"))
async def farm_plant(q: CallbackQuery):
    _, _, page, num, egg_type = q.data.split(":")
    page, num = int(page), int(num)
    chat_id, uid = q.message.chat.id, q.from_user.id
    row = c.execute("SELECT count FROM farm_inventory WHERE chat_id=? AND user_id=? AND egg_type=?", (chat_id, uid, egg_type)).fetchone()
    if not row or row[0] <= 0:
        return await q.answer("❌ دیگه از این نداری!", show_alert=True)
    c.execute("UPDATE farm_inventory SET count=count-1 WHERE chat_id=? AND user_id=? AND egg_type=?", (chat_id, uid, egg_type))
    c.execute("INSERT INTO farm_tiles(chat_id,user_id,plot_num,egg_type,planted_at) VALUES(?,?,?,?,?)", (chat_id, uid, num, egg_type, int(time.time())))
    bump_farm_record(chat_id, uid, eggs_planted=1)
    db.commit()
    await q.answer(f"🌱 {EGGS[egg_type]['name']} کاشته شد!")
    await q.message.edit_text(build_farm_text(chat_id, uid), reply_markup=build_farm_keyboard(chat_id, uid, page))

# ===== رنک مزرعه بر اساس وضعیت فعلی پنل =====
FARM_RANK_TIERS = [
    (0,    "🌱 نوزمین‌دار"),
    (100,  "🌾 کشاورز"),
    (300,  "🚜 تراکتوردار"),
    (700,  "🏭 صنعتی"),
    (1500, "👑 پادشاه مزرعه"),
]

def egg_avg_value(egg_type):
    info = EGGS[egg_type]
    return (info["min"] + info["max"]) / 2

def get_farm_tier(worth):
    tier = FARM_RANK_TIERS[0][1]
    for threshold, name in FARM_RANK_TIERS:
        if worth >= threshold:
            tier = name
    return tier

def compute_farm_stats(chat_id, uid):
    """محاسبه‌ی زنده‌ی وضعیت فعلی مزرعه: هرچی الان تو زمین‌ها و انبار داره."""
    row = c.execute("SELECT farm_plots FROM users WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()
    plots_owned = row[0] if row else 2
    tiles = c.execute("SELECT egg_type,planted_at FROM farm_tiles WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchall()
    growing = ready = spoiled = 0
    tile_worth = 0.0
    for egg_type, planted_at in tiles:
        state, _ = tile_state(egg_type, planted_at)
        val = egg_avg_value(egg_type)
        if state == "growing":
            growing += 1
            tile_worth += val
        elif state == "ready":
            ready += 1
            tile_worth += val
        else:
            spoiled += 1
            tile_worth += val / 2
    inv = get_inventory(chat_id, uid)
    inv_count = sum(inv.values())
    inv_worth = sum(egg_avg_value(t) * n for t, n in inv.items())
    net_worth = round(tile_worth + inv_worth)
    return {
        "plots_owned": plots_owned, "growing": growing, "ready": ready, "spoiled": spoiled,
        "planted": len(tiles), "inv_count": inv_count, "net_worth": net_worth,
    }

@dp.message(Command("farmrank"))
async def farm_rank_cmd(m: Message):
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    st = compute_farm_stats(m.chat.id, m.from_user.id)
    rec = get_farm_record(m.chat.id, m.from_user.id)
    tier = get_farm_tier(st["net_worth"])
    await m.reply(
        f"🌾 رنک مزرعه‌ی شما\n\n"
        f"{tier}\n"
        f"💰 ارزش خالص فعلی: {st['net_worth']} اسپرم (تخمینی)\n"
        f"🟫 زمین‌های باز: {st['plots_owned']}\n"
        f"🌱 کاشته‌شده الان: {st['planted']} (⏳{st['growing']} در حال رشد | ✅{st['ready']} آماده | 💀{st['spoiled']} فاسد)\n"
        f"📦 تخم تو انبار: {st['inv_count']}\n\n"
        f"📜 رکورد کلی (از اول تا الان):\n"
        f"🥚 تخم خریده: {rec['eggs_bought']}\n"
        f"🌱 تخم کاشته: {rec['eggs_planted']}\n"
        f"🌾 تخم برداشت‌کرده: {rec['eggs_harvested']}\n"
        f"💰 کل اسپرم به‌دست‌اومده از برداشت: {rec['total_harvest_value']}\n"
        f"🟫 کل زمین‌های باز‌شده: {rec['plots_opened']}\n"
        f"💸 کل سانت خرج‌شده تو مزرعه: {rec['spent_cm']}"
    )

@dp.message(Command("farmleader"))
async def farm_leaderboard(m: Message):
    chat_id = m.chat.id
    candidates = c.execute(
        "SELECT DISTINCT user_id FROM ("
        "  SELECT user_id FROM farm_tiles WHERE chat_id=?"
        "  UNION SELECT user_id FROM farm_inventory WHERE chat_id=? AND count>0"
        "  UNION SELECT user_id FROM users WHERE chat_id=? AND farm_plots>2"
        ")", (chat_id, chat_id, chat_id)
    ).fetchall()
    if not candidates:
        return await m.reply("📭 هنوز کسی تو این گروه مزرعه راه ننداخته!")
    board = []
    for (uid,) in candidates:
        st = compute_farm_stats(chat_id, uid)
        board.append((uid, st))
    board.sort(key=lambda x: x[1]["net_worth"], reverse=True)
    txt = "📊 جدول رنک مزرعه‌ی این گروه\n\n"
    for i, (uid, st) in enumerate(board[:10], 1):
        name = get_name(chat_id, uid)
        rec = get_farm_record(chat_id, uid)
        tier = get_farm_tier(st["net_worth"])
        txt += (
            f"{i}. {name}\n"
            f"   {tier}\n"
            f"   💰 ارزش الان: {st['net_worth']} اسپرم | 🟫 زمین: {st['plots_owned']} | "
            f"🌱 کاشته: {st['planted']} (✅{st['ready']}/⏳{st['growing']}/💀{st['spoiled']}) | 📦 انبار: {st['inv_count']}\n"
            f"   📜 رکورد: {rec['eggs_harvested']} برداشت | {rec['total_harvest_value']} اسپرم کل | {rec['plots_opened']} زمین باز‌شده\n\n"
        )
    await m.reply(txt)

@dp.message(Command("loan"))
async def loan(m:Message):
    try:
        amt=int(m.text.split()[1])
    except:
        return await m.reply("Reply to a user and use /loan 5")

    if not m.reply_to_message:
        return await m.reply("Reply to a user and use /loan 5")

    lender=m.from_user.id
    borrower=m.reply_to_message.from_user.id

    if lender==borrower:
        return await m.reply("You can't loan yourself.")

    user(m.chat.id,lender,m.from_user.full_name)
    user(m.chat.id,borrower,m.reply_to_message.from_user.full_name)

    s=get_size(m.chat.id,lender)

    if s<amt:
        return await m.reply("Not enough cm.")

    day_ago = int(time.time()) - 24*60*60
    borrowed_today = c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM loans WHERE chat_id=? AND borrower_id=? AND loan_time>?",
        (m.chat.id, borrower, day_ago)
    ).fetchone()[0]

    if borrowed_today + amt > 50:
        remaining = max(0, 50 - borrowed_today)
        return await m.reply(
            f"❌ این کاربر امروز {borrowed_today} سانت وام گرفته!\n"
            f"حداکثر روزانه ۵۰ سانته.\n"
            f"{'دیگه نمیتونه وام بگیره!' if remaining == 0 else f'فقط {remaining} سانت دیگه میتونه بگیره.'}"
        )

    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(amt,m.chat.id,lender))
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(amt,m.chat.id,borrower))
    c.execute("INSERT INTO loans(chat_id,lender_id,borrower_id,amount,loan_time) VALUES(?,?,?,?,?)",(m.chat.id,lender,borrower,amt,int(time.time())))
    db.commit()

    await m.reply(f"💸 وام انجام شد!\n\nمقدار: {amt} سانت\n📊 این کاربر امروز {borrowed_today+amt}/50 سانت وام گرفته")

@dp.message(Command("repay"))
async def repay(m:Message):
    try: amt=int(m.text.split()[1])
    except: return await m.reply("Usage: /repay 5")
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    s,d=c.execute("SELECT size,debt FROM users WHERE chat_id=? AND user_id=?",(m.chat.id,m.from_user.id)).fetchone()
    amt=min(amt,s,d)
    c.execute("UPDATE users SET size=?,debt=? WHERE chat_id=? AND user_id=?",(s-amt,d-amt,m.chat.id,m.from_user.id)); db.commit()
    await m.reply(f"✅ Repaid {amt} cm")

@dp.message(Command("top"))
async def top(m:Message):
    rows=c.execute("SELECT name,size FROM users WHERE chat_id=? ORDER BY size DESC LIMIT 10",(m.chat.id,)).fetchall()
    txt="🏆 جدول بزرگان\n\n"
    for i,(n,s) in enumerate(rows,1): txt+=f"{i}. {n} — {s} سانت\n"
    await m.reply(txt)

PVP_MIN_GAMES = 3          # قبل از این تعداد دوئل، اصلاً تصحیح انجام نمی‌شه (شانس خام 50-50 می‌مونه)
PVP_BALANCE_STRENGTH = 0.6 # هرچقدر بیشتر، میل به 50٪ قوی‌تره (0 = بی‌اثر، 1 = خیلی قوی)
PVP_PROB_MIN = 0.25        # کف احتمال برد (هیچ‌وقت شانس کسی از این کمتر نمی‌شه)
PVP_PROB_MAX = 0.75        # سقف احتمال برد (هیچ‌وقت شانس کسی از این بیشتر نمی‌شه)

def get_pvp_winrate(chat_id, uid):
    row = c.execute("SELECT wins,losses FROM pvp_stats WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()
    if not row:
        return 0.5, 0
    wins, losses = row
    games = wins + losses
    if games < PVP_MIN_GAMES:
        return 0.5, games
    return wins / games, games

def bump_pvp_stats(chat_id, winner, loser):
    for uid, w, l in ((winner, 1, 0), (loser, 0, 1)):
        c.execute(
            "INSERT INTO pvp_stats(chat_id,user_id,wins,losses) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET wins=wins+excluded.wins, losses=losses+excluded.losses",
            (chat_id, uid, w, l)
        )

# ===== سیستم رنک =====
RANK_DAILY_QUOTA = 6          # حداقل دوئل لازم در روز تا رنک نریزه
RANK_WIN_RP = 20              # امتیاز رنک برای برد
RANK_LOSS_RP = 10             # امتیاز رنک کم‌شده برای باخت
RANK_DECAY_PER_MISSED_DAY = 15  # ریزش رنک به‌ازای هر روزی که حدنصاب رعایت نشده
RANK_TIERS = [
    (0,    "🥒 نوپا"),
    (100,  "🌭 آماتور"),
    (250,  "🍆 حرفه‌ای"),
    (500,  "🚀 استاد"),
    (900,  "👑 افسانه‌ای"),
]

def today_str():
    return time.strftime("%Y-%m-%d", time.gmtime())

def get_rank_tier(rp):
    tier = RANK_TIERS[0][1]
    for threshold, name in RANK_TIERS:
        if rp >= threshold:
            tier = name
    return tier

def ensure_rank_row(chat_id, uid):
    row = c.execute("SELECT rp,duels_today,last_active_day FROM rank_stats WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()
    if not row:
        c.execute("INSERT INTO rank_stats(chat_id,user_id,rp,duels_today,last_active_day) VALUES(?,?,0,0,?)", (chat_id, uid, today_str()))
        db.commit()
        return 0, 0, today_str()
    return row

def apply_daily_decay(chat_id, uid):
    """اگه از آخرین باری که دوئل زده روز(ها) گذشته باشه و اون روزها حدنصاب رعایت نشده، رنک می‌ریزه."""
    rp, duels_today, last_day = ensure_rank_row(chat_id, uid)
    today = today_str()
    if last_day == today:
        return rp, duels_today
    try:
        gap = (
            time.mktime(time.strptime(today, "%Y-%m-%d")) -
            time.mktime(time.strptime(last_day, "%Y-%m-%d"))
        ) / 86400
        gap = int(round(gap))
    except Exception:
        gap = 1
    if gap < 1:
        gap = 1
    missed_days = gap if duels_today < RANK_DAILY_QUOTA else gap - 1
    if missed_days > 0:
        rp = max(0, rp - missed_days * RANK_DECAY_PER_MISSED_DAY)
    c.execute("UPDATE rank_stats SET rp=?,duels_today=0,last_active_day=? WHERE chat_id=? AND user_id=?", (rp, today, chat_id, uid))
    db.commit()
    return rp, 0

def record_rank_duel(chat_id, winner, loser):
    for uid, delta in ((winner, RANK_WIN_RP), (loser, -RANK_LOSS_RP)):
        rp, duels_today = apply_daily_decay(chat_id, uid)
        rp = max(0, rp + delta)
        duels_today += 1
        c.execute("UPDATE rank_stats SET rp=?,duels_today=? WHERE chat_id=? AND user_id=?", (rp, duels_today, chat_id, uid))
    db.commit()

@dp.message(Command("rank"))
async def rank_cmd(m: Message):
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    rp, duels_today = apply_daily_decay(m.chat.id, m.from_user.id)
    tier = get_rank_tier(rp)
    left = max(0, RANK_DAILY_QUOTA - duels_today)
    quota_txt = "✅ حدنصاب امروز رعایت شده!" if left == 0 else f"⚠️ {left} دوئل دیگه مونده تا رنکت فردا نریزه!"
    await m.reply(
        f"🏅 رنک شما\n\n"
        f"{tier}\n"
        f"📊 امتیاز: {rp} RP\n"
        f"⚔️ دوئل امروز: {duels_today}/{RANK_DAILY_QUOTA}\n\n"
        f"{quota_txt}"
    )

@dp.message(Command("ranktop"))
async def rank_top(m: Message):
    rows = c.execute(
        "SELECT user_id,rp FROM rank_stats WHERE chat_id=? ORDER BY rp DESC LIMIT 10", (m.chat.id,)
    ).fetchall()
    if not rows:
        return await m.reply("📭 هنوز کسی تو این گروه دوئل نزده!")
    txt = "🏆 برترین‌های رنک این گروه\n\n"
    for i, (uid, rp) in enumerate(rows, 1):
        name = get_name(m.chat.id, uid)
        txt += f"{i}. {name} — {rp} RP ({get_rank_tier(rp)})\n"
    await m.reply(txt)

@dp.message(Command("pvp"))
async def pvp(m:Message):
    try: bet=int(m.text.split()[1])
    except: return await m.reply("Usage: /pvp 30")
    if bet<=0: return await m.reply("❌ مقدار شرط باید عدد مثبت باشه!")
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    s=get_size(m.chat.id,m.from_user.id)
    if s<bet: return await m.reply("Not enough cm.")
    cur = c.execute(
        "INSERT INTO battles(creator,bet) VALUES(?,?)",
        (m.from_user.id, bet)
    )
    db.commit()
    bid=cur.lastrowid
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚔️ قبول دوئل",callback_data=f"pvp:{bid}")]])
    await m.reply(f"⚔️ دوئل مرگبار!\n\n💰 شرط: {bet} سانت\n\nبرنده همه رو می‌بره!",reply_markup=kb)

@dp.callback_query(F.data.startswith("pvp:"))
async def accept(q:CallbackQuery):
    bid=int(q.data.split(":")[1])
    row=c.execute("SELECT creator,bet,active FROM battles WHERE id=?",(bid,)).fetchone()
    if not row or row[2]==0: return await q.answer("Expired")
    creator,bet,_=row
    if q.from_user.id==creator: return await q.answer("Not yourself")
    chat_id=q.message.chat.id
    user(chat_id,q.from_user.id,q.from_user.full_name)
    s1=get_size(chat_id,creator)
    s2=get_size(chat_id,q.from_user.id)
    if s1<bet or s2<bet: return await q.answer("Not enough cm")
    opponent = q.from_user.id
    wr_creator, _ = get_pvp_winrate(chat_id, creator)
    wr_opponent, _ = get_pvp_winrate(chat_id, opponent)
    # هرکی نرخ بردش بالاتر از 50٪ باشه شانسش کم می‌شه، هرکی پایین‌تره شانسش زیاد می‌شه
    p_creator = 0.5 + PVP_BALANCE_STRENGTH * (wr_opponent - wr_creator)
    p_creator = max(PVP_PROB_MIN, min(PVP_PROB_MAX, p_creator))
    winner = creator if random.random() < p_creator else opponent
    loser = opponent if winner == creator else creator
    bump_pvp_stats(chat_id, winner, loser)
    record_rank_duel(chat_id, winner, loser)
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(bet,chat_id,winner))
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(bet,chat_id,loser))
    c.execute("UPDATE battles SET active=0 WHERE id=?",(bid,))
    db.commit()
    winner_name=get_name(chat_id,winner)
    await q.message.edit_text(f"🏆 پایان دوئل!\n\n👑 برنده: {winner_name}\n💰 جایزه: {bet} سانت\n🏅 +{RANK_WIN_RP} RP برنده | -{RANK_LOSS_RP} RP بازنده\n\n😂 بازنده باید بیشتر تمرین کنه!")


# ===== Mafia Team PvP System =====
c.execute("CREATE TABLE IF NOT EXISTS mafia_battles(id INTEGER PRIMARY KEY AUTOINCREMENT,creator INTEGER,opponent INTEGER,bet INTEGER,active INTEGER DEFAULT 1,chat_id INTEGER)")
c.execute("CREATE TABLE IF NOT EXISTS mafia_members(battle_id INTEGER,user_id INTEGER,name TEXT,team INTEGER)")
db.commit()

def mafia_counts(bid):
    t1=c.execute("SELECT COUNT(*) FROM mafia_members WHERE battle_id=? AND team=1",(bid,)).fetchone()[0]
    t2=c.execute("SELECT COUNT(*) FROM mafia_members WHERE battle_id=? AND team=2",(bid,)).fetchone()[0]
    return t1,t2

def mafia_board(bid,creator_name,opp_name,bet,status="🔫 جنگ مافیا شروع شد!"):
    t1,t2=mafia_counts(bid)
    return (
        f"{status}\n\n"
        f"🔴 تیم {creator_name}  در مقابل  🔵 تیم {opp_name}\n"
        f"💰 ورودی هر نفر: {bet} سانت\n\n"
        f"هر کی میخواد وارد بشه دکمه تیمش رو بزنه (مخفیانه ثبت میشه، تیم‌بندی معلوم نیست)!\n"
        f"👥 تعداد شرکت‌کننده‌ها: {t1+t2}\n\n"
        f"وقتی آماده بودید، سازنده یا حریف دکمه «شروع نبرد» رو بزنه."
    )

@dp.message(Command("mafia"))
async def mafia_start(m:Message):
    if not m.reply_to_message:
        return await m.reply("⚠️ روی پیام حریف ریپلای کن و بنویس /mafia [مبلغ]\nمثال: /mafia 5")
    try:
        bet=int(m.text.split()[1])
    except:
        return await m.reply("⚠️ استفاده: /mafia [مبلغ]\nمثال: /mafia 5")
    if bet<=0:
        return await m.reply("❌ مبلغ باید مثبت باشه!")
    creator=m.from_user.id
    opponent=m.reply_to_message.from_user.id
    if creator==opponent:
        return await m.reply("❌ نمیتونی با خودت مافیا بازی کنی!")
    if m.reply_to_message.from_user.is_bot:
        return await m.reply("❌ نمیتونی با بات مافیا بازی کنی!")
    user(m.chat.id,creator,m.from_user.full_name)
    user(m.chat.id,opponent,m.reply_to_message.from_user.full_name)
    s=get_size(m.chat.id,creator)
    if s<bet:
        return await m.reply("❌ سانت کافی نداری!")
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(bet,m.chat.id,creator))
    cur=c.execute("INSERT INTO mafia_battles(creator,opponent,bet,chat_id) VALUES(?,?,?,?)",(creator,opponent,bet,m.chat.id))
    db.commit()
    bid=cur.lastrowid
    c.execute("INSERT INTO mafia_members(battle_id,user_id,name,team) VALUES(?,?,?,1)",(bid,creator,m.from_user.full_name))
    db.commit()
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔴 تیم {m.from_user.full_name}",callback_data=f"mjoin:{bid}:1"),
         InlineKeyboardButton(text=f"🔵 تیم {m.reply_to_message.from_user.full_name}",callback_data=f"mjoin:{bid}:2")],
        [InlineKeyboardButton(text="🏁 شروع نبرد و شمارش",callback_data=f"mstart:{bid}")]
    ])
    await m.reply(
        mafia_board(bid,m.from_user.full_name,m.reply_to_message.from_user.full_name,bet),
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("mjoin:"))
async def mafia_join(q:CallbackQuery):
    try:
        _,bid,team=q.data.split(":")
        bid=int(bid); team=int(team)
        row=c.execute("SELECT creator,opponent,bet,active,chat_id FROM mafia_battles WHERE id=?",(bid,)).fetchone()
        if not row or row[3]==0:
            return await q.answer("⚠️ این نبرد تموم شده!",show_alert=True)
        creator,opponent,bet,_,chat_id=row
        uid=q.from_user.id
        already=c.execute("SELECT team FROM mafia_members WHERE battle_id=? AND user_id=?",(bid,uid)).fetchone()
        if already:
            return await q.answer("⚠️ قبلاً مخفیانه وارد یه تیم شدی!",show_alert=True)
        user(chat_id,uid,q.from_user.full_name)
        s=get_size(chat_id,uid)
        if s<bet:
            return await q.answer("❌ سانت کافی نداری!",show_alert=True)
        c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(bet,chat_id,uid))
        c.execute("INSERT INTO mafia_members(battle_id,user_id,name,team) VALUES(?,?,?,?)",(bid,uid,q.from_user.full_name,team))
        db.commit()
        creator_name=get_name(chat_id,creator)
        opp_name=get_name(chat_id,opponent)
        await q.answer("✅ مخفیانه وارد تیم شدی!")
        try:
            await q.message.edit_text(
                mafia_board(bid,creator_name,opp_name,bet),
                reply_markup=q.message.reply_markup
            )
        except Exception:
            pass
    except Exception:
        logger.exception("mafia_join crashed for data=%s", q.data)
        try:
            await q.answer("⚠️ یه خطا پیش اومد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass

@dp.callback_query(F.data.startswith("mstart:"))
async def mafia_resolve(q:CallbackQuery):
    try:
        bid=int(q.data.split(":")[1])
        row=c.execute("SELECT creator,opponent,bet,active,chat_id FROM mafia_battles WHERE id=?",(bid,)).fetchone()
        if not row or row[3]==0:
            return await q.answer("⚠️ این نبرد تموم شده!",show_alert=True)
        creator,opponent,bet,_,chat_id=row
        if q.from_user.id not in (creator,opponent):
            return await q.answer("⚠️ فقط سازنده یا حریف میتونه نبرد رو شروع کنه!",show_alert=True)
        members=c.execute("SELECT user_id,name,team FROM mafia_members WHERE battle_id=?",(bid,)).fetchall()
        team1=[(u,n) for u,n,t in members if t==1]
        team2=[(u,n) for u,n,t in members if t==2]
        tie=len(team1)==len(team2)
        if len(team1)>len(team2):
            winners,losers,wteam=team1,team2,1
        elif len(team2)>len(team1):
            winners,losers,wteam=team2,team1,2
        else:
            wteam=random.choice([1,2])
            winners,losers=(team1,team2) if wteam==1 else (team2,team1)
        # یارهای خریداری‌شده از کمپانی کیر شناسه‌ی منفی دارن: توی شمارش تیم حساب میشن ولی سهمی از جایزه نمی‌برن
        human_count=sum(1 for u,_,_t in members if u>0)
        winners_human=[(u,n) for u,n in winners if u>0]
        total_pot=bet*human_count
        share=total_pot//len(winners_human) if winners_human else 0
        for uid,_ in winners_human:
            c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(share,chat_id,uid))
        c.execute("UPDATE mafia_battles SET active=0 WHERE id=?",(bid,))
        db.commit()
        win_names="، ".join(n for _,n in winners) or "-"
        lose_names="، ".join(n for _,n in losers) or "-"
        color="🔴" if wteam==1 else "🔵"
        await q.message.edit_text(
            f"🔫 پایان جنگ مافیا!\n\n"
            f"{color} تیم برنده ({len(winners)} نفر): {win_names}\n"
            f"💀 تیم بازنده ({len(losers)} نفر): {lose_names}\n\n"
            f"💰 کل جایزه: {total_pot} سانت\n"
            f"🎁 سهم هر برنده: {share} سانت\n\n"
            f"{'😂 مساوی بودن، شانس تصمیم گرفت!' if tie else '👑 تیم بزرگتر برد!'}",
            reply_markup=None
        )
        await q.answer("🏁 نبرد تموم شد!")
    except Exception:
        logger.exception("mafia_resolve crashed for data=%s", q.data)
        try:
            await q.answer("⚠️ یه خطا پیش اومد، دوباره امتحان کن یا با /mafia یه نبرد جدید بساز.", show_alert=True)
        except Exception:
            pass


# ===== Mafia2 Team PvP (reveals only who won, not the team lineup) =====
c.execute("CREATE TABLE IF NOT EXISTS mafia2_battles(id INTEGER PRIMARY KEY AUTOINCREMENT,creator INTEGER,opponent INTEGER,bet INTEGER,active INTEGER DEFAULT 1,chat_id INTEGER)")
c.execute("CREATE TABLE IF NOT EXISTS mafia2_members(battle_id INTEGER,user_id INTEGER,name TEXT,team INTEGER)")
db.commit()

# ستون is_ally برای مشخص کردن یارهایی که از کمپانی کیر خریداری شدن (نه بازیکن واقعی)
# یارها همیشه user_id منفی دارن، پس منطق برد/باخت با همون علامتِ منفی هم قابل تشخیصه؛
# این ستون فقط برای خوانایی/آمار اضافه‌ست.
for _tbl in ("mafia_members", "mafia2_members"):
    try:
        c.execute(f"ALTER TABLE {_tbl} ADD COLUMN is_ally INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
db.commit()

def mafia2_counts(bid):
    t1=c.execute("SELECT COUNT(*) FROM mafia2_members WHERE battle_id=? AND team=1",(bid,)).fetchone()[0]
    t2=c.execute("SELECT COUNT(*) FROM mafia2_members WHERE battle_id=? AND team=2",(bid,)).fetchone()[0]
    return t1,t2

def mafia2_board(bid,creator_name,opp_name,bet,status="🔫 جنگ مافیا شروع شد!"):
    t1,t2=mafia2_counts(bid)
    return (
        f"{status}\n\n"
        f"🔴 تیم {creator_name}  در مقابل  🔵 تیم {opp_name}\n"
        f"💰 ورودی هر نفر: {bet} سانت\n\n"
        f"هر کی میخواد وارد بشه دکمه تیمش رو بزنه (مخفیانه ثبت میشه، تیم‌بندی معلوم نیست)!\n"
        f"👥 تعداد شرکت‌کننده‌ها: {t1+t2}\n\n"
        f"وقتی آماده بودید، سازنده یا حریف دکمه «شروع نبرد» رو بزنه.\n"
        f"ℹ️ در پایان فقط برنده مشخص میشه، نه اینکه کی کجا بود."
    )

@dp.message(Command("mafia2"))
async def mafia2_start(m:Message):
    if not m.reply_to_message:
        return await m.reply("⚠️ روی پیام حریف ریپلای کن و بنویس /mafia2 [مبلغ]\nمثال: /mafia2 5")
    try:
        bet=int(m.text.split()[1])
    except:
        return await m.reply("⚠️ استفاده: /mafia2 [مبلغ]\nمثال: /mafia2 5")
    if bet<=0:
        return await m.reply("❌ مبلغ باید مثبت باشه!")
    creator=m.from_user.id
    opponent=m.reply_to_message.from_user.id
    if creator==opponent:
        return await m.reply("❌ نمیتونی با خودت مافیا بازی کنی!")
    if m.reply_to_message.from_user.is_bot:
        return await m.reply("❌ نمیتونی با بات مافیا بازی کنی!")
    user(m.chat.id,creator,m.from_user.full_name)
    user(m.chat.id,opponent,m.reply_to_message.from_user.full_name)
    s=get_size(m.chat.id,creator)
    if s<bet:
        return await m.reply("❌ سانت کافی نداری!")
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(bet,m.chat.id,creator))
    cur=c.execute("INSERT INTO mafia2_battles(creator,opponent,bet,chat_id) VALUES(?,?,?,?)",(creator,opponent,bet,m.chat.id))
    db.commit()
    bid=cur.lastrowid
    c.execute("INSERT INTO mafia2_members(battle_id,user_id,name,team) VALUES(?,?,?,1)",(bid,creator,m.from_user.full_name))
    db.commit()
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔴 تیم {m.from_user.full_name}",callback_data=f"m2join:{bid}:1"),
         InlineKeyboardButton(text=f"🔵 تیم {m.reply_to_message.from_user.full_name}",callback_data=f"m2join:{bid}:2")],
        [InlineKeyboardButton(text="🏁 شروع نبرد و شمارش",callback_data=f"m2start:{bid}")]
    ])
    await m.reply(
        mafia2_board(bid,m.from_user.full_name,m.reply_to_message.from_user.full_name,bet),
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("m2join:"))
async def mafia2_join(q:CallbackQuery):
    try:
        _,bid,team=q.data.split(":")
        bid=int(bid); team=int(team)
        row=c.execute("SELECT creator,opponent,bet,active,chat_id FROM mafia2_battles WHERE id=?",(bid,)).fetchone()
        if not row or row[3]==0:
            return await q.answer("⚠️ این نبرد تموم شده!",show_alert=True)
        creator,opponent,bet,_,chat_id=row
        uid=q.from_user.id
        already=c.execute("SELECT team FROM mafia2_members WHERE battle_id=? AND user_id=?",(bid,uid)).fetchone()
        if already:
            return await q.answer("⚠️ قبلاً مخفیانه وارد یه تیم شدی!",show_alert=True)
        user(chat_id,uid,q.from_user.full_name)
        s=get_size(chat_id,uid)
        if s<bet:
            return await q.answer("❌ سانت کافی نداری!",show_alert=True)
        c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(bet,chat_id,uid))
        c.execute("INSERT INTO mafia2_members(battle_id,user_id,name,team) VALUES(?,?,?,?)",(bid,uid,q.from_user.full_name,team))
        db.commit()
        creator_name=get_name(chat_id,creator)
        opp_name=get_name(chat_id,opponent)
        await q.answer("✅ مخفیانه وارد تیم شدی!")
        try:
            await q.message.edit_text(
                mafia2_board(bid,creator_name,opp_name,bet),
                reply_markup=q.message.reply_markup
            )
        except Exception:
            pass
    except Exception:
        logger.exception("mafia2_join crashed for data=%s", q.data)
        try:
            await q.answer("⚠️ یه خطا پیش اومد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass

@dp.callback_query(F.data.startswith("m2start:"))
async def mafia2_resolve(q:CallbackQuery):
    try:
        bid=int(q.data.split(":")[1])
        row=c.execute("SELECT creator,opponent,bet,active,chat_id FROM mafia2_battles WHERE id=?",(bid,)).fetchone()
        if not row or row[3]==0:
            return await q.answer("⚠️ این نبرد تموم شده!",show_alert=True)
        creator,opponent,bet,_,chat_id=row
        if q.from_user.id not in (creator,opponent):
            return await q.answer("⚠️ فقط سازنده یا حریف میتونه نبرد رو شروع کنه!",show_alert=True)
        members=c.execute("SELECT user_id,name,team FROM mafia2_members WHERE battle_id=?",(bid,)).fetchall()
        team1=[(u,n) for u,n,t in members if t==1]
        team2=[(u,n) for u,n,t in members if t==2]
        tie=len(team1)==len(team2)
        if len(team1)>len(team2):
            winners,wteam=team1,1
        elif len(team2)>len(team1):
            winners,wteam=team2,2
        else:
            wteam=random.choice([1,2])
            winners=team1 if wteam==1 else team2
        # یارهای خریداری‌شده از کمپانی کیر شناسه‌ی منفی دارن: توی شمارش تیم حساب میشن ولی سهمی از جایزه نمی‌برن
        human_count=sum(1 for u,_,_t in members if u>0)
        winners_human=[(u,n) for u,n in winners if u>0]
        total_pot=bet*human_count
        share=total_pot//len(winners_human) if winners_human else 0
        for uid,_ in winners_human:
            c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(share,chat_id,uid))
        c.execute("UPDATE mafia2_battles SET active=0 WHERE id=?",(bid,))
        db.commit()
        creator_name=get_name(chat_id,creator)
        opp_name=get_name(chat_id,opponent)
        win_label=f"🔴 تیم {creator_name}" if wteam==1 else f"🔵 تیم {opp_name}"
        await q.message.edit_text(
            f"🔫 پایان جنگ مافیا!\n\n"
            f"👥 مجموع شرکت‌کننده‌ها: {len(members)} نفر\n\n"
            f"👑 برنده: {win_label} ({len(winners)} نفر)\n"
            f"💰 کل جایزه: {total_pot} سانت\n"
            f"🎁 سهم هر برنده: {share} سانت\n\n"
            f"{'😂 مساوی بودن، شانس تصمیم گرفت!' if tie else '👑 تیم بزرگتر برد!'}\n\n"
            f"🤫 اینکه کی تو کدوم تیم بود مخفی می‌مونه!",
            reply_markup=None
        )
        await q.answer("🏁 نبرد تموم شد!")
    except Exception:
        logger.exception("mafia2_resolve crashed for data=%s", q.data)
        try:
            await q.answer("⚠️ یه خطا پیش اومد، دوباره امتحان کن یا با /mafia2 یه نبرد جدید بساز.", show_alert=True)
        except Exception:
            pass


# ===== Celebrity Collection System =====
c.execute("""CREATE TABLE IF NOT EXISTS collections(
    chat_id INTEGER,
    user_id INTEGER,
    celeb TEXT,
    paid_price INTEGER DEFAULT 0,
    locked INTEGER DEFAULT 0
)""")
db.commit()


CELEBS = {
    "realtrelilove": ("PH",360,300,"AgACAgQAAxkBAAEiyFBqsl-5jTz16APdmObyCkIx7YxhswAClQ5rG7ngkFFdW6GmZb8jHQEAAwIAA3MAAz0E"),
    "Reislin": ("PH",360,300,"AgACAgQAAxkBAAEiyFJqsmCJV0lfG4fGyV1NAzacB8Cw-QACmA5rG7ngkFF8Fc3TvW79agEAAwIAA3MAAz0E"),
    "Jenny Kitty": ("PH",360,300,"AgACAgQAAxkBAAEii1tqpsVgDm25Nn44iH4e6bjwlTu2DQACAhBrGxHWOFF506Hdb2bopwEAAwIAA3MAAz0E"),
    "Eva Elfie": ("PH",360,300,"AgACAgQAAxkBAAEii1FqpsQmac1sr7zDK5Xt_G_FbeOM7AACARBrGxHWOFFJWAjEYvCMBwEAAwIAA3MAAz0E"),
    "Eden Ivy": ("PH",360,300,"AgACAgQAAxkBAAEixFxqsbxspcJ6O37kDI4Ha-S_3gGe6AACEBFrG9E-QVHRkUKIZY82rQEAAwIAA3MAAz0E"),
    "Ana Stangle": ("PH",360,300,"AgACAgQAAxkBAAEiixhqprxb_z1tsYNO_q36AAFG7bhvcrcAAu8PaxsR1jhR-H1AmjEhOykBAAMCAANtAAM9BA"),
    "Ana Stangle": ("PH",360,300,"AgACAgQAAxkBAAEiixhqprxb_z1tsYNO_q36AAFG7bhvcrcAAu8PaxsR1jhR-H1AmjEhOykBAAMCAANtAAM9BA"),
    "Polly Yangs": ("PH",360,300,"AgACAgQAAxkBAAEiixZqprwWMSc06gjR6Wf1AfjEMS-bYgAC7g9rGxHWOFF1rqHV_XmcqQEAAwIAA3MAAz0E"),
    "Mia Malkova": ("PH",360,300,"AgACAgQAAxkBAAEiixJqprvaxcqdkRMMLmDFpVVVJ22ycwAC7Q9rGxHWOFHDv3vxo0jSfAEAAwIAA3MAAz0E"),
    "Lyli Philips": ("PH",360,300,"AgACAgQAAxkBAAEiiwpqpruE7F2IrBlMo2Z7kJ7-iGC3hgAC6g9rGxHWOFEDhS0dv-NIdQEAAwIAA3MAAz0E"),
    "Remida": ("PH",360,300,"AgACAgQAAxkBAAEiiwZqprtRYUg6S4AaVWga3nd9oplYegAC6Q9rGxHWOFELW3Aa_kVp9QEAAwIAA3MAAz0E"),
    "Lily Lou": ("PH",360,300,"AgACAgQAAxkBAAEiiv5qprr3COm1SOlfeWjvRoU-Dmz9AgAC6A9rGxHWOFFkcd5sl-uEqwEAAwIAA3MAAz0E"),
    "Lena Paul": ("PH",360,300,"AgACAgQAAxkBAAEiivZqprk0NUKUAwABNROBmv06cviy35UAAvgVaxs3EDhR-L2oRxHNDegBAAMCAANzAAM9BA"),
    "Angela White": ("PH",360,300,"AgACAgQAAxkBAAEiK8tqlX0p5xvvc7RkL8yCDK50C70cMAAC3hBrG4IDsFBEhOBKjPVnEgEAAwIAA3MAAz0E"),
    "Comatozze": ("PH",360,300,"AgACAgQAAxkBAAEiK99qlYOdZfkvzeW8xB9yg8ay0a5E0AAC4w9rG8igqVDkAAH7iUzCMUoBAAMCAANzAAM9BA"),
    "Sweetie Fox": ("PH",360,300,"AgACAgQAAxkBAAEiK-NqlYTmIgPdooRd2A-cfBNbZIDWDwAC5g9rG8igqVDv7lHTLO7LEgEAAwIAA3MAAz0E"),
    "Diana Rider": ("PH",360,300,"AgACAgQAAxkBAAEiiyZqpr0Z6wHI0orDFTTA0FpqPDKxcgAC8g9rGxHWOFEEh3EQeHYcWwEAAwIAA3MAAz0E"),
    "Lana Rhoades": ("PH",360,300,"AgACAgQAAxkBAAEiK-dqlYZjT3rDHHtNt5EPHZTb_o70xwAC6A9rG8igqVCMQM6VNiWu-wEAAwIAA3MAAz0E"),
    "Ana de Armas": ("S",310,250,"AgACAgQAAxkBAAEiLBRqlYks3xjU5rQCkNXypUQOoS9n3QAC7w9rG8igqVAhpyUv1rvdmwEAAwIAA3MAAz0E"),
    "Kylie Jenner": ("S",310,250,"AgACAgQAAxkBAAEii0NqpsFgJgN8lSjVTyiKBQanxRUV7AAC_Q9rGxHWOFHpMFeQR7I8fQEAAwIAA3MAAz0E"),
    "Sydney Sweeney": ("S",310,250,"AgACAgQAAxkBAAEii0tqpsL7OwOzbu-UicheoiKjfQGvEgAC_w9rGxHWOFEfc0vRD4_BvAEAAwIAA3MAAz0E"),
    "Pinkchyu": ("S",310,250,"AgACAgQAAxkBAAEiiz9qpsDeoCCTvNfIyfhp_0DLu6md9gAC-g9rGxHWOFEwehVusIFDyAEAAwIAA3MAAz0E"),
    "Georgina Rodriguez": ("S",310,250,"AgACAgQAAxkBAAEii0FqpsEls89dT1QptHWD0jEdIhaOXQAC_A9rGxHWOFGfJx1nk0t_GAEAAwIAA3MAAz0E"),
    "Madison Beer": ("A",200,150,"AgACAgQAAxkBAAEiix5qpry4N-XRCC1BeUeGrBacwO1PxwAC8Q9rGxHWOFEiXTj03FeFEAEAAwIAA3MAAz0E"),
    "Sadie Sink": ("A",200,150,"AgACAgQAAxkBAAEiLFJqlZEGt1mG9G15PgKP4PPhQcRm-gACARBrG8igqVCvW3ZnrKAGywEAAwIAA3MAAz0E"),
    "Scarlett Johansson": ("A",200,150,"AgACAgQAAxkBAAEijSBqpwXof-3vjZeK_61sIm9brMrWWwAC5Q9rG_HjOVGRBdU_77rF8wEAAwIAA3MAAz0E"),
    "Anne Hathaway": ("B",300,150,"AgACAgQAAxkBAAEiLHJqlZLNXRVWSN-k7xu-doS7FTDsbgACCRBrG8igqVDk8fgU3uXUFwEAAwIAA3MAAz0E"),
    "Elizabeth Olsen": ("B",300,150,"AgACAgQAAxkBAAEiLHRqlZNem1Ue0rp7IJ182xumcv2XKwACChBrG8igqVDyu9zwySffJAEAAwIAA3MAAz0E"),
    "Olivia Rodrigo": ("B",300,150,"AgACAgQAAxkBAAEiLHZqlZPReBV1oF2fHUcz1MiKfWTuPAACCxBrG8igqVDsVdcPA4GFUAEAAwIAA3MAAz0E"),
    "Emma Watson": ("B",300,150,"AgACAgQAAxkBAAEiLHpqlZRQeIXIaNZcdp3gXLdrXT2anAACDRBrG8igqVC08ITVIg9XMAEAAwIAA3MAAz0E"),
    "Leah Halton": ("B",300,150,"AgACAgQAAxkBAAEiiyhqpr1II4KnLYGLp4wwWO25ZUjnMwAC8w9rGxHWOFHnxgG03-TRhAEAAwIAA3MAAz0E"),
    "Ashlyn Castro": ("B",300,150,"AgACAgQAAxkBAAEiiypqpr2gKFikU1gkAAFPisEKVaAKA5oAAvQPaxsR1jhRLB-aZnzbFkIBAAMCAANtAAM9BA"),
    "Kristen Stewart": ("B",300,150,"AgACAgQAAxkBAAEiLHxqlZSorrGyLnSOBdRqRqdnSvnaXgACDxBrG8igqVDQHpTLZKuoOwEAAwIAA3MAAz0E"),
    "Olivia Cooke": ("A",200,150,"AgACAgQAAxkBAAEiLFBqlZC2pdCvovgiG6aqLJwG7oNBHAAC_w9rG8igqVC9yhxFuB9gDQEAAwIAA3MAAz0E"),
    "Sabrina Carpenter": ("B",200,100,"AgACAgQAAxkBAAEiLGxqlZJxN_AeZTfMK1e_iZUiC4tvaAACBxBrG8igqVD1mfDtce21cAEAAwIAA3MAAz0E"),
    "Dua Lipa": ("A",200,150,"AgACAgQAAxkBAAEiLIRqlZW2x2U46kRw5iGd8GMcDrH5xAACFxBrG8igqVBqy7bfQ8pbLgEAAwIAA3MAAz0E"),
    "Sophie Tatcher": ("A",200,150,"AgACAgQAAxkBAAEiLExqlZBP5UH5p9rTTAUQ6hv3_mUpkAAC_g9rG8igqVABEzpZosgbbAEAAwIAA3MAAz0E"),
    "Billie Eilish": ("S",310,250,"AgACAgQAAxkBAAEii01qpsO9jfgwqGNaCOuxcDI2B_tolAACLRFrG-cKCVH7fF__m9zGrQEAAwIAA3MAAz0E"),
    "Folorance Pugh": ("B",100,50,"AgACAgQAAxkBAAEiizBqpr485700NYU6jwetz6qFBbBYrgAC9g9rGxHWOFFkMl6wX7RergEAAwIAA3MAAz0E"),
}


TIER_CELEBS = {
    "S": [(n, v[1], v[3]) for n, v in CELEBS.items() if v[0] == "S"],
    "A": [(n, v[1], v[3]) for n, v in CELEBS.items() if v[0] == "A"],
    "B": [(n, v[1], v[3]) for n, v in CELEBS.items() if v[0] == "B"],
    "PH": [(n, v[1], v[3]) for n, v in CELEBS.items() if v[0] == "PH"],
}
TIER_LABELS = {
    "S": "🥇 Tier S",
    "A": "🥈 Tier A",
    "B": "🥉 Tier B",
    "PH": "💎 Tier PH",
}
TIER_PRICES = {"S": (310, 250), "A": (200, 150), "B": (100, 50), "PH": (360, 300)}

def build_market_caption(tier, page):
    celebs = TIER_CELEBS[tier]
    name, price, photo = celebs[page]
    buy_price, spin_price = TIER_PRICES[tier]
    label = TIER_LABELS[tier]
    txt = (
        f"🛒 بازار سلبریتی\n"
        f"{label} — صفحه {page+1}/{len(celebs)}\n\n"
        f"👑 {name}\n"
        f"💰 خرید: {price} سانت\n"
        f"🎰 اسپین: {spin_price} سانت\n\n"
        f"🛒 /buy {name}\n"
        f"🎰 /spin {tier.lower()}"
    )
    return txt, photo

def build_market_kb(tier, page):
    celebs = TIER_CELEBS[tier]
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"mkt:{tier}:{page-1}"))
    if page < len(celebs) - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"mkt:{tier}:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

async def fetch_photo(url: str) -> BufferedInputFile | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    filename = url.split("/")[-1]
                    return BufferedInputFile(data, filename=filename)
    except Exception:
        pass
    return None

# ===== Telegram file_id photo cache (avoid re-downloading/re-uploading every time) =====
# این جدول عمداً سراسریه (نه بر اساس چت) چون فقط عکس‌های سلبریتی‌ها رو کش می‌کنه،
# نه دیتای اقتصادی کاربر؛ عکس‌ها تو همه‌ی گروه‌ها یکسانن.
c.execute("CREATE TABLE IF NOT EXISTS photo_cache(url TEXT PRIMARY KEY, file_id TEXT)")
db.commit()

async def resolve_photo(url: str):
    """Accepts either a Telegram file_id (used directly, zero download) or an http(s) URL
    (downloaded once, then cached as a file_id from then on)."""
    if not url:
        return None
    if not url.startswith("http://") and not url.startswith("https://"):
        # Already a Telegram file_id — use it straight away, nothing to download.
        return url
    row = c.execute("SELECT file_id FROM photo_cache WHERE url=?", (url,)).fetchone()
    if row:
        return row[0]
    return await fetch_photo(url)

def cache_photo(url: str, sent_message):
    """Call after successfully sending/editing a photo that was NOT already cached, to store its file_id."""
    try:
        if sent_message and sent_message.photo:
            c.execute("INSERT OR REPLACE INTO photo_cache(url,file_id) VALUES(?,?)", (url, sent_message.photo[-1].file_id))
            db.commit()
    except Exception:
        pass

@dp.message(Command("getfileid"))
async def get_file_id(m: Message):
    target = m.reply_to_message.photo[-1] if (m.reply_to_message and m.reply_to_message.photo) else (m.photo[-1] if m.photo else None)
    if not target:
        return await m.reply("⚠️ یه عکس بفرست (یا روی یه عکس ریپلای بزن) و /getfileid رو بنویس.")
    await m.reply(f"🆔 file_id:\n`{target.file_id}`", parse_mode="Markdown")

@dp.message(Command("market"))
async def market(m:Message):
    for tier in ["S", "A", "B", "PH"]:
        if not TIER_CELEBS[tier]:
            continue
        txt, photo_url = build_market_caption(tier, 0)
        kb = build_market_kb(tier, 0)
        photo = await resolve_photo(photo_url) if photo_url else None
        try:
            if photo:
                sent = await m.bot.send_photo(m.chat.id, photo, caption=txt, reply_markup=kb)
                if isinstance(photo, BufferedInputFile):
                    cache_photo(photo_url, sent)
            else:
                await m.bot.send_message(m.chat.id, txt, reply_markup=kb)
        except Exception as e:
            # اگه فرستادن عکس خراب شد (مثلاً file_id نامعتبره) کل دستور /market نباید بترکه؛
            # به‌جاش پیام متنی می‌فرستیم تا بقیه‌ی تیرها هم نمایش داده بشن.
            print(f"[market] خطا در ارسال عکس تیر {tier}: {e}")
            await m.bot.send_message(m.chat.id, txt, reply_markup=kb)

@dp.callback_query(F.data.startswith("mkt:"))
async def market_page_nav(q: CallbackQuery):
    _, tier, page = q.data.split(":")
    page = int(page)
    txt, photo_url = build_market_caption(tier, page)
    kb = build_market_kb(tier, page)
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo and q.message.photo:
            edited = await q.message.edit_media(
                media=InputMediaPhoto(media=photo, caption=txt),
                reply_markup=kb
            )
            if isinstance(photo, BufferedInputFile) and hasattr(edited, "photo"):
                cache_photo(photo_url, edited)
        elif q.message.photo:
            await q.message.edit_caption(caption=txt, reply_markup=kb)
        else:
            await q.message.edit_text(txt, reply_markup=kb)
    except Exception as e:
        await q.answer(f"خطا: {e}", show_alert=True)
        return
    await q.answer()

@dp.message(Command("collection"))
async def collection(m:Message):
    if m.reply_to_message:
        target = m.reply_to_message.from_user
        user(m.chat.id, target.id, target.full_name)
        rows = c.execute("SELECT celeb FROM collections WHERE chat_id=? AND user_id=?", (m.chat.id, target.id)).fetchall()
        if not rows:
            return await m.reply(f"📚 {target.full_name} هنوز چیزی نداره.")
        celebs = [r[0] for r in rows]
        await send_collection_page(m.chat.id, target.id, celebs, 0, m.bot, viewer_id=m.from_user.id)
    else:
        user(m.chat.id, m.from_user.id, m.from_user.full_name)
        rows = c.execute("SELECT celeb FROM collections WHERE chat_id=? AND user_id=?", (m.chat.id, m.from_user.id)).fetchall()
        if not rows:
            return await m.reply("📚 هنوز چیزی نداری.")
        celebs = [r[0] for r in rows]
        await send_collection_page(m.chat.id, m.from_user.id, celebs, 0, m.bot, viewer_id=m.from_user.id)

async def send_collection_page(chat_id, owner_id, celebs, page, bot, viewer_id=None):
    name = celebs[page]
    tier, price, spin, photo_url = CELEBS[name]
    tier_label = {"S": "🥇 S", "A": "🥈 A", "B": "🥉 B", "PH": "💎 PH"}[tier]
    txt = (
        f"📚 کالکشن — {page+1}/{len(celebs)}\n\n"
        f"👑 {name}\n"
        f"🏅 تیر: {tier_label}\n"
        f"💰 ارزش: {price} سانت\n\n"
        f"🛒 /sell {name}\n"
        f"🏪 /list {name} [قیمت]"
    )
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"col:{owner_id}:{page-1}"))
    if page < len(celebs) - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"col:{owner_id}:{page+1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo:
            sent = await bot.send_photo(chat_id, photo, caption=txt, reply_markup=kb)
            if isinstance(photo, BufferedInputFile):
                cache_photo(photo_url, sent)
        else:
            await bot.send_message(chat_id, txt, reply_markup=kb)
    except Exception as e:
        print(f"[collection] خطا در ارسال عکس {name}: {e}")
        await bot.send_message(chat_id, txt, reply_markup=kb)

@dp.callback_query(F.data.startswith("col:"))
async def collection_nav(q: CallbackQuery):
    _, owner_id, page = q.data.split(":")
    owner_id = int(owner_id)
    page = int(page)
    chat_id = q.message.chat.id
    # allow anyone to browse
    rows = c.execute("SELECT celeb FROM collections WHERE chat_id=? AND user_id=?", (chat_id, owner_id)).fetchall()
    celebs = [r[0] for r in rows]
    if page >= len(celebs):
        page = len(celebs) - 1
    name = celebs[page]
    tier, price, spin, photo_url = CELEBS[name]
    tier_label = {"S": "🥇 S", "A": "🥈 A", "B": "🥉 B", "PH": "💎 PH"}[tier]
    txt = (
        f"📚 کالکشن — {page+1}/{len(celebs)}\n\n"
        f"👑 {name}\n"
        f"🏅 تیر: {tier_label}\n"
        f"💰 ارزش: {price} سانت\n\n"
        f"🛒 /sell {name}\n"
        f"🏪 /list {name} [قیمت]"
    )
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"col:{owner_id}:{page-1}"))
    if page < len(celebs) - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"col:{owner_id}:{page+1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo and q.message.photo:
            edited = await q.message.edit_media(
                media=InputMediaPhoto(media=photo, caption=txt),
                reply_markup=kb
            )
            if isinstance(photo, BufferedInputFile) and hasattr(edited, "photo"):
                cache_photo(photo_url, edited)
        elif q.message.photo:
            await q.message.edit_caption(caption=txt, reply_markup=kb)
        else:
            await q.message.edit_text(txt, reply_markup=kb)
    except Exception as e:
        await q.answer(f"خطا: {e}", show_alert=True)
        return
    await q.answer()


@dp.message(Command("buy"))
async def buy(m:Message):
    name=strip_command(m.text)

    if name not in CELEBS:
        return await m.reply("❌ سلبریتی پیدا نشد.")

    tier,price,spin,photo=CELEBS[name]

    user(m.chat.id,m.from_user.id,m.from_user.full_name)

    size=get_size(m.chat.id,m.from_user.id)

    if size<price:
        return await m.reply("💸 سانت کافی نداری!")

    tier_key = CELEBS[name][0]

    # check if user already owns it
    user_owned = c.execute("SELECT 1 FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, m.from_user.id, name)).fetchone()
    if user_owned:
        return await m.reply("📚 این سلبریتی رو داری!")

    # tier S and A are exclusive — check if anyone owns it (locked or not)
    # tier B is only exclusive if locked
    if tier_key in ("S", "A"):
        owned = c.execute("SELECT user_id FROM collections WHERE chat_id=? AND celeb=?", (m.chat.id, name)).fetchone()
        if owned:
            owner_name = get_name(m.chat.id, owned[0])
            return await m.reply(f"❌ این سلبریتی قبلاً توسط {owner_name} خریداری شده!")
    else:
        # tier B — only block if locked
        locked = c.execute("SELECT user_id FROM collections WHERE chat_id=? AND celeb=? AND locked=1", (m.chat.id, name)).fetchone()
        if locked:
            owner_name = get_name(m.chat.id, locked[0])
            return await m.reply(f"🔒 این سلبریتی توسط {owner_name} قفل شده!")

    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(price,m.chat.id,m.from_user.id))
    c.execute("INSERT INTO collections(chat_id,user_id,celeb,paid_price) VALUES(?,?,?,?)",(m.chat.id,m.from_user.id,name,price))
    db.commit()

    photo_url = photo
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo:
            sent = await m.bot.send_photo(m.chat.id, photo, caption=f"🎉 خرید موفق!\n\n👑 {name}")
            if isinstance(photo, BufferedInputFile):
                cache_photo(photo_url, sent)
        else:
            await m.reply(f"🎉 خرید موفق!\n\n👑 {name}")
    except Exception as e:
        # خرید توی دیتابیس قبلاً ثبت شده، پس حتی اگه عکس نره حداقل تاییدیه‌ی متنی بره
        print(f"[buy] خطا در ارسال عکس {name}: {e}")
        await m.reply(f"🎉 خرید موفق!\n\n👑 {name}")

@dp.message(Command("spin"))
async def spin(m:Message):
    try:
        tier=m.text.split()[1].upper()
    except:
        return await m.reply("استفاده: /spin s | a | b")

    prices={"S":250,"A":150,"B":50,"PH":300}

    if tier not in prices:
        return await m.reply("Tier باید s یا a یا b باشد.")

    cost=prices[tier]

    user(m.chat.id,m.from_user.id,m.from_user.full_name)

    size=get_size(m.chat.id,m.from_user.id)

    if size<cost:
        return await m.reply("💸 سانت کافی نداری!")

    pool=[n for n,v in CELEBS.items() if v[0]==tier]
    celeb=random.choice(pool)

    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?",(cost,m.chat.id,m.from_user.id))

    spin_tier = CELEBS[celeb][0]
    user_owned = c.execute("SELECT 1 FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, m.from_user.id, celeb)).fetchone()
    if user_owned:
        c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(cost,m.chat.id,m.from_user.id))
        db.commit()
        return await m.reply(f"🔄 این سلبریتی رو قبلاً داری!\n\n👑 {celeb}\n💰 {cost} سانت برگشت داده شد.")

    if spin_tier in ("S", "A"):
        owned = c.execute("SELECT user_id FROM collections WHERE chat_id=? AND celeb=?", (m.chat.id, celeb)).fetchone()
        if owned:
            c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(cost,m.chat.id,m.from_user.id))
            db.commit()
            owner_name=get_name(m.chat.id,owned[0])
            return await m.reply(f"🔄 این سلبریتی قبلاً توسط {owner_name} خریداری شده!\n\n👑 {celeb}\n💰 {cost} سانت برگشت داده شد.")
    else:
        locked = c.execute("SELECT user_id FROM collections WHERE chat_id=? AND celeb=? AND locked=1", (m.chat.id, celeb)).fetchone()
        if locked:
            c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(cost,m.chat.id,m.from_user.id))
            db.commit()
            owner_name=get_name(m.chat.id,locked[0])
            return await m.reply(f"🔒 این سلبریتی توسط {owner_name} قفل شده!\n\n👑 {celeb}\n💰 {cost} سانت برگشت داده شد.")

    # ===== ارزش فروش (paid_price) برای سلبریتی گرفته‌شده از اسپین =====
    # تیر B → نصف هزینه‌ی اسپین، بقیه‌ی تیرها (S/A/PH) → ۷۵٪ هزینه‌ی اسپین.
    resale_value = cost // 2 if spin_tier == "B" else (cost * 3) // 4

    c.execute(
        "INSERT INTO collections(chat_id,user_id,celeb,paid_price) VALUES(?,?,?,?)",
        (m.chat.id,m.from_user.id,celeb,resale_value)
    )
    db.commit()

    photo_url=CELEBS[celeb][3]
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo:
            sent = await m.bot.send_photo(m.chat.id, photo, caption=f"🎰 اسپین موفق!\n\n👑 {celeb}")
            if isinstance(photo, BufferedInputFile):
                cache_photo(photo_url, sent)
        else:
            await m.reply(f"🎰 اسپین موفق!\n\n👑 {celeb}")
    except Exception as e:
        print(f"[spin] خطا در ارسال عکس {celeb}: {e}")
        await m.reply(f"🎰 اسپین موفق!\n\n👑 {celeb}")

@dp.message(Command("collectors"))
async def collectors(m:Message):
    rows=c.execute("""
        SELECT users.name,COUNT(collections.celeb) AS total
        FROM users
        LEFT JOIN collections
        ON users.user_id=collections.user_id AND users.chat_id=collections.chat_id
        WHERE users.chat_id=?
        GROUP BY users.chat_id,users.user_id
        ORDER BY total DESC
        LIMIT 10
    """,(m.chat.id,)).fetchall()

    txt="🏆 بهترین کلکسیونرها\n\n"

    for i,(name,total) in enumerate(rows,1):
        txt+=f"{i}. {name} — {total} سلبریتی\n"

    await m.reply(txt)


@dp.message(Command("list"))
async def list_celeb(m:Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        return await m.reply("Usage: /list [نام] [قیمت]\nمثال: /list Kylie Jenner 500")
    try:
        rest = parts[1].rsplit(None, 1)
        name = rest[0].strip()
        price = int(rest[1])
    except:
        return await m.reply("Usage: /list [نام] [قیمت]\nمثال: /list Kylie Jenner 500")
    if name not in CELEBS:
        return await m.reply("❌ سلبریتی پیدا نشد.")
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    owned = c.execute("SELECT 1 FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, m.from_user.id, name)).fetchone()
    if not owned:
        return await m.reply("❌ این سلبریتی رو نداری!")
    # cancel any previous listing for this celeb
    c.execute("UPDATE listings SET active=0 WHERE chat_id=? AND seller_id=? AND celeb=?", (m.chat.id, m.from_user.id, name))
    cur = c.execute("INSERT INTO listings(chat_id, seller_id, celeb, price) VALUES(?,?,?,?)", (m.chat.id, m.from_user.id, name, price))
    db.commit()
    lid = cur.lastrowid
    tier, orig_price, spin, photo_url = CELEBS[name]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🛒 خرید به قیمت {price} سانت", callback_data=f"buyoff:{lid}")
    ]])
    caption = (
        f"🏪 فروش سلبریتی!\n\n"
        f"👑 {name}\n"
        f"💰 قیمت: {price} سانت\n"
        f"👤 فروشنده: {m.from_user.full_name}"
    )
    photo = await resolve_photo(photo_url) if photo_url else None
    try:
        if photo:
            sent = await m.bot.send_photo(m.chat.id, photo, caption=caption, reply_markup=kb)
            if isinstance(photo, BufferedInputFile):
                cache_photo(photo_url, sent)
        else:
            await m.reply(caption, reply_markup=kb)
    except Exception as e:
        print(f"[list] خطا در ارسال عکس {name}: {e}")
        await m.reply(caption, reply_markup=kb)

@dp.callback_query(F.data.startswith("buyoff:"))
async def buyoff(q: CallbackQuery):
    lid = int(q.data.split(":")[1])
    row = c.execute("SELECT chat_id, seller_id, celeb, price, active FROM listings WHERE id=?", (lid,)).fetchone()
    if not row or row[4] == 0:
        return await q.answer("❌ این آگهی دیگه فعال نیست!", show_alert=True)
    chat_id, seller_id, name, price, _ = row
    buyer_id = q.from_user.id
    if buyer_id == seller_id:
        return await q.answer("❌ نمیتونی از خودت بخری!", show_alert=True)
    user(chat_id, buyer_id, q.from_user.full_name)
    buyer_size = get_size(chat_id, buyer_id)
    if buyer_size < price:
        return await q.answer("❌ سانت کافی نداری!", show_alert=True)
    # transfer
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (price, chat_id, buyer_id))
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?", (price, chat_id, seller_id))
    c.execute("DELETE FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (chat_id, seller_id, name))
    c.execute("INSERT INTO collections(chat_id, user_id, celeb) VALUES(?,?,?)", (chat_id, buyer_id, name))
    c.execute("UPDATE listings SET active=0 WHERE id=?", (lid,))
    db.commit()
    seller_name = get_name(chat_id, seller_id)
    await q.message.edit_caption(
        caption=(
            f"✅ معامله انجام شد!\n\n"
            f"👑 {name}\n"
            f"💰 قیمت: {price} سانت\n"
            f"🛒 خریدار: {q.from_user.full_name}\n"
            f"💸 فروشنده: {seller_name}"
        )
    )
    await q.answer("✅ خرید موفق!")

@dp.message(Command("sell"))
async def sell(m:Message):
    name=strip_command(m.text)
    if name not in CELEBS:
        return await m.reply("❌ سلبریتی پیدا نشد.")
    user(m.chat.id,m.from_user.id,m.from_user.full_name)
    owned=c.execute(
        "SELECT paid_price FROM collections WHERE chat_id=? AND user_id=? AND celeb=?",
        (m.chat.id,m.from_user.id,name)
    ).fetchone()
    if not owned:
        return await m.reply("❌ این سلبریتی رو نداری!")
    paid=owned[0]
    c.execute("DELETE FROM collections WHERE chat_id=? AND user_id=? AND celeb=?",(m.chat.id,m.from_user.id,name))
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?",(paid,m.chat.id,m.from_user.id))
    db.commit()
    await m.reply(f"💸 فروش موفق!\n\n👑 {name}\n💰 {paid} سانت به حسابت اضافه شد!")

LOCK_COSTS = {"PH": 75, "S": 60, "A": 50, "B": 25}

@dp.message(Command("lock"))
async def lock_celeb(m:Message):
    name = strip_command(m.text)
    if name not in CELEBS:
        return await m.reply("❌ سلبریتی پیدا نشد.")
    tier_key = CELEBS[name][0]
    if tier_key not in LOCK_COSTS:
        return await m.reply("❌ این سلبریتی نیاز به قفل نداره!")
    lock_cost = LOCK_COSTS[tier_key]
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    owned = c.execute("SELECT locked FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, m.from_user.id, name)).fetchone()
    if not owned:
        return await m.reply("❌ این سلبریتی رو نداری!")
    if owned[0] == 1:
        return await m.reply("🔒 این سلبریتی قبلاً قفله!")
    size = get_size(m.chat.id, m.from_user.id)
    if size < lock_cost:
        return await m.reply(f"❌ برای قفل کردن به {lock_cost} سانت نیاز داری!")
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (lock_cost, m.chat.id, m.from_user.id))
    c.execute("UPDATE collections SET locked=1 WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, m.from_user.id, name))
    db.commit()
    await m.reply(f"🔒 {name} قفل شد!\n\n💰 {lock_cost} سانت کسر شد.\nحالا کسی دیگه نمیتونه این سلبریتی رو بخره.")

@dp.message(Command("gloan"))
async def gloan(m:Message):
    try:
        amt = int(m.text.split()[1])
    except:
        return await m.reply("Usage: /gloan [مقدار]\nمثال: /gloan 50")
    if amt < 1 or amt > 50:
        return await m.reply("❌ حداکثر وام از بازی 50 سانته!")
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    existing = c.execute("SELECT amount, due_time FROM game_loans WHERE chat_id=? AND user_id=?", (m.chat.id, m.from_user.id)).fetchone()
    if existing:
        due = existing[1]
        rem = (due - int(time.time())) // 3600
        return await m.reply(f"❌ قبلاً {existing[0]} سانت وام داری!\n⏳ {rem} ساعت تا موعد پرداخت")
    due_time = int(time.time()) + 24*60*60
    c.execute("INSERT OR REPLACE INTO game_loans(chat_id, user_id, amount, due_time) VALUES(?,?,?,?)", (m.chat.id, m.from_user.id, amt, due_time))
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?", (amt, m.chat.id, m.from_user.id))
    db.commit()
    await m.reply(
        f"💰 وام از بازی\n\n"
        f"💵 مقدار: {amt} سانت\n"
        f"⏳ مهلت پرداخت: ۲۴ ساعت\n\n"
        f"برای پرداخت: /gpay {amt}"
    )

@dp.message(Command("gpay"))
async def gpay(m:Message):
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    loan = c.execute("SELECT amount, due_time FROM game_loans WHERE chat_id=? AND user_id=?", (m.chat.id, m.from_user.id)).fetchone()
    if not loan:
        return await m.reply("❌ وامی نداری!")
    amt, due_time = loan
    size = get_size(m.chat.id, m.from_user.id)
    if size < amt:
        return await m.reply(f"❌ سانت کافی نداری! باید {amt} سانت داشته باشی.")
    c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (amt, m.chat.id, m.from_user.id))
    c.execute("DELETE FROM game_loans WHERE chat_id=? AND user_id=?", (m.chat.id, m.from_user.id))
    db.commit()
    await m.reply(f"✅ وام {amt} سانت پرداخت شد!")

async def check_loans(bot):
    while True:
        await asyncio.sleep(60)
        now = int(time.time())
        overdue = c.execute("SELECT chat_id, user_id, amount FROM game_loans WHERE due_time<?", (now,)).fetchall()
        for chat_id, uid, amt in overdue:
            size_row = c.execute("SELECT size FROM users WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()
            if not size_row:
                continue
            size = size_row[0]
            paid = 0
            msg = f"⚠️ وام {amt} سانت موعدش گذشت!\n\n"
            # sell celebs to cover debt
            if size < amt:
                celebs = c.execute("SELECT celeb, paid_price FROM collections WHERE chat_id=? AND user_id=? ORDER BY paid_price DESC", (chat_id, uid)).fetchall()
                for celeb, paid_price in celebs:
                    if paid >= amt:
                        break
                    c.execute("DELETE FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (chat_id, uid, celeb))
                    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?", (paid_price, chat_id, uid))
                    paid += paid_price
                    msg += f"💸 {celeb} فروخته شد (+{paid_price} سانت)\n"
                db.commit()
                size = c.execute("SELECT size FROM users WHERE chat_id=? AND user_id=?", (chat_id, uid)).fetchone()[0]
            # deduct what we can
            deduct = min(amt, size)
            c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (deduct, chat_id, uid))
            remaining = amt - deduct
            if remaining > 0:
                c.execute("UPDATE users SET size=size-? WHERE chat_id=? AND user_id=?", (remaining, chat_id, uid))
                msg += f"📉 {remaining} سانت بدهی — حساب منفی شد!"
            else:
                msg += f"✅ {amt} سانت کسر شد."
            c.execute("DELETE FROM game_loans WHERE chat_id=? AND user_id=?", (chat_id, uid))
            db.commit()
            try:
                await bot.send_message(uid, msg)
            except:
                pass


@dp.message(Command("addcm"))
async def addcm(m:Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    try:
        parts = m.text.split()
        amount = int(parts[1])
    except:
        return await m.reply("Usage: /addcm [amount] (reply to a user)")
    if not m.reply_to_message:
        return await m.reply("Reply to a user to add cm.")
    target = m.reply_to_message.from_user.id
    user(m.chat.id, target, m.reply_to_message.from_user.full_name)
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?", (amount, m.chat.id, target))
    db.commit()
    new_size = get_size(m.chat.id, target)
    await m.reply(f"✅ {amount} سانت به {m.reply_to_message.from_user.full_name} اضافه شد!\n📏 اندازه جدید: {new_size} سانت")

@dp.message(Command("luckydrop"))
async def lucky_drop(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    prize = 50
    try:
        parts = m.text.split()
        if len(parts) > 1:
            prize = int(parts[1])
    except:
        pass
    players = c.execute("SELECT user_id,name FROM users WHERE chat_id=?", (m.chat.id,)).fetchall()
    if not players:
        return await m.reply("❌ هنوز کسی تو این گروه بازی نکرده که قرعه‌کشی بشه.")
    winner_id, winner_name = random.choice(players)
    c.execute("UPDATE users SET size=size+? WHERE chat_id=? AND user_id=?", (prize, m.chat.id, winner_id))
    db.commit()
    new_size = get_size(m.chat.id, winner_id)
    await m.reply(f"🎉 قرعه‌کشی رندوم!\n\n🍆 {winner_name} خوش‌شانس بود و {prize} سانت مجانی گرفت!\n📏 اندازه‌ی جدیدش: {new_size} سانت")

@dp.message(Command("resetgroup"))
async def reset_group(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    parts = m.text.split()
    if len(parts) < 2 or parts[1] != "CONFIRM":
        return await m.reply(
            "⚠️ این کار همه‌ی داده‌های این گروه رو کاملاً و برای همیشه پاک می‌کنه:\n"
            "سایز/اسپرم همه، رنک، مافیا، بورس، کلکسیون سلبریتی‌ها، وام‌ها و مزرعه.\n\n"
            "اگه مطمئنی، بنویس:\n/resetgroup CONFIRM"
        )
    chat_id = m.chat.id
    # جدول‌هایی که مستقیم ستون chat_id دارن
    for table in (
        "users", "pvp_stats", "rank_stats", "loans", "listings",
        "game_loans", "mafia_battles", "mafia2_battles", "collections",
        "company_rounds", "company_options", "company_investments",
        "company_participants", "owned_companies",
        "farm_tiles", "farm_inventory", "farm_records",
    ):
        c.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
    # جدول‌هایی که فقط از طریق battle_id به گروه وصلن
    c.execute("DELETE FROM mafia_members WHERE battle_id IN (SELECT id FROM mafia_battles WHERE chat_id=?)", (chat_id,))
    c.execute("DELETE FROM mafia2_members WHERE battle_id IN (SELECT id FROM mafia2_battles WHERE chat_id=?)", (chat_id,))
    db.commit()
    await m.reply("✅ همه‌ی داده‌های این گروه پاک شد. بازی از صفر شروع می‌شه.")


async def addsperm(m:Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    try:
        parts = m.text.split()
        amount = int(parts[1])
    except:
        return await m.reply("Usage: /addsperm [amount] (reply to a user)")
    if not m.reply_to_message:
        return await m.reply("Reply to a user to add sperm.")
    target = m.reply_to_message.from_user.id
    user(m.chat.id, target, m.reply_to_message.from_user.full_name)
    c.execute("UPDATE users SET sperm=sperm+? WHERE chat_id=? AND user_id=?", (amount, m.chat.id, target))
    db.commit()
    new_sperm = get_sperm(m.chat.id, target)
    await m.reply(f"✅ {amount} اسپرم به {m.reply_to_message.from_user.full_name} اضافه شد!\n🧬 اسپرم جدید: {new_sperm}")


@dp.message(Command("addspermall"))
async def addsperm_all(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    try:
        amount = int(m.text.split()[1])
    except:
        return await m.reply("Usage: /addspermall [amount]\nمثال: /addspermall 100")
    c.execute("UPDATE users SET sperm=sperm+? WHERE chat_id=?", (amount, m.chat.id))
    affected = c.rowcount
    db.commit()
    await m.reply(f"✅ {amount} اسپرم به همه‌ی {affected} کاربر این گروه اضافه شد!")


@dp.message(Command("addcb"))
async def addcb(m:Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ دسترسی ندارید!")
    raw = strip_command(m.text)
    if not raw:
        return await m.reply(
            "Usage: /addcb [نام سلبریتی] (reply to a user)\nمثال: /addcb Dua Lipa\n\n"
            "برای گرفتن سلبریتی از کسی، یه منفی جلوش بذار:\nمثال: /addcb -Dua Lipa"
        )
    remove_mode = raw.startswith("-")
    name = raw[1:].strip() if remove_mode else raw
    if name not in CELEBS:
        return await m.reply("❌ سلبریتی پیدا نشد.")
    if not m.reply_to_message:
        return await m.reply("Reply to a user to give/take the celeb.")
    target = m.reply_to_message.from_user.id
    user(m.chat.id, target, m.reply_to_message.from_user.full_name)
    already = c.execute("SELECT 1 FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, target, name)).fetchone()
    if remove_mode:
        if not already:
            return await m.reply(f"❌ {m.reply_to_message.from_user.full_name} اصلاً این سلبریتی رو نداره!")
        c.execute("DELETE FROM collections WHERE chat_id=? AND user_id=? AND celeb=?", (m.chat.id, target, name))
        db.commit()
        return await m.reply(f"🗑 {name} از {m.reply_to_message.from_user.full_name} گرفته شد!")
    if already:
        return await m.reply(f"❌ {m.reply_to_message.from_user.full_name} این سلبریتی رو داره!")
    tier, price, spin, photo = CELEBS[name]
    c.execute("INSERT INTO collections(chat_id,user_id,celeb,paid_price,locked) VALUES(?,?,?,?,0)", (m.chat.id, target, name, price))
    db.commit()
    await m.reply(f"✅ {name} به {m.reply_to_message.from_user.full_name} داده شد!\n👑 Tier {tier}")


# ================== کمپانی کیر (بازار بورس) ==================
# فرضیات پیاده‌سازی (اگه فرق داشت بگو عوض کنم):
# - فقط ادمین بازار رو باز/بسته می‌کنه (/copen و /cclose)
# - بین دوتا /copen هیچ محدودیت زمانی وجود نداره؛ ادمین هر وقت خواست باز/بسته می‌کنه
# - حداقل بودجه‌ی هر شرکت رندومه (بین ۵ تا ۱۵ سانت) و برای هر شرکت جدا تعیین میشه
# - هر سرمایه‌گذاری باید حداقل ۱۰٪ سایز لحظه‌ای فرد باشه؛ سقفی برای حداکثرش نیست (نامحدود)
# - شرکتی که بیشترین سرمایه رو جمع کرده حذف میشه (گنده شده, ترکیده) + شرکتی که به حداقل بودجه‌ی خودش نرسیده
# - بین بقیه، بیشترین سهم‌دار هر شرکت، همون شرکت رو با کل سرمایه‌ی جمع‌شده توش می‌بره (بقیه سرمایه‌شون رو از دست میدن)
# - هر شرکت بعد از برد، روزی یه بار ۱۰٪ ارزشش سود میده
# - هر شرکت ۲ یا ۳ "یار" رندوم داره که صاحبش می‌تونه با /hire بخره و با /useyar بفرسته توی نبرد مافیای فعلیش
#   (یار فقط تعداد تیم رو زیاد می‌کنه؛ سهمی از جایزه‌ی نقدی نمی‌بره تا اقتصاد بازی خراب نشه)
# - /cashout نصف ارزش فعلی شرکت رو نقد می‌کنه و از ارزش شرکت (و سود روزانه‌ی بعدیش) کم می‌کنه

COMPANY_COOLDOWN = 0                # محدودیت زمانی باز کردن بازار حذف شده؛ فقط ادمین کنترل می‌کند
COMPANY_MIN_INVEST_PCT = 0.10       # کف سرمایه‌گذاری هر نفر توی هر بار /invest: ۱۰٪ سایزش (نه سقف!)
COMPANY_MIN_BUDGET_LOW = 5          # پایین‌ترین مقدار ممکن برای حداقل بودجه‌ی یه شرکت
COMPANY_MIN_BUDGET_HIGH = 15        # بالاترین مقدار ممکن برای حداقل بودجه‌ی یه شرکت
COMPANY_DIVIDEND_PCT = 0.20         # سود روزانه‌ی شرکت به صاحبش
COMPANY_WORKER_COST = 15            # قیمت خرید هر یار مافیا از شرکت

COMPANY_NAME_POOL = [
    "هلدینگ کلفت‌کاران", "شرکت درازگستر", "گروه صنعتی گردن‌کلفت",
    "کارخانه شق‌القمر", "شرکت راست‌قامتان", "هلدینگ قدبلند پلاس",
    "گروه بازرگانی ستون‌محکم", "شرکت خشتک‌پاره",
    "کارخانه سه‌سانتی و شرکا", "هلدینگ قدکش‌ها",
    "شرکت دراز و دردسر", "گروه صنعتی چوب‌خط",
    "شرکت کلفت‌گستر شرق", "کارخانه میله‌محکم",
    "هلدینگ صاف‌و‌صوف", "شرکت گردن‌کلفت غرب",
    "گروه صنعتی خط‌کش‌طلایی", "شرکت قدبلندستان",
    "کارخانه ستون‌سازان", "هلدینگ اندازه‌گیران",
]

c.execute("""CREATE TABLE IF NOT EXISTS company_rounds(
    chat_id INTEGER PRIMARY KEY,
    round_id INTEGER DEFAULT 0,
    status TEXT DEFAULT 'closed',
    open_time INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS company_options(
    chat_id INTEGER,
    round_id INTEGER,
    slot INTEGER,
    name TEXT,
    min_budget INTEGER DEFAULT 20,
    PRIMARY KEY(chat_id, round_id, slot)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS company_investments(
    chat_id INTEGER,
    round_id INTEGER,
    slot INTEGER,
    user_id INTEGER,
    amount INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, round_id, slot, user_id)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS company_participants(
    chat_id INTEGER,
    round_id INTEGER,
    user_id INTEGER,
    cap INTEGER,
    invested INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, round_id, user_id)
)""")
c.execute("""CREATE TABLE IF NOT EXISTS owned_companies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    owner_id INTEGER,
    name TEXT,
    value INTEGER,
    workers INTEGER,
    workers_bought INTEGER DEFAULT 0,
    workers_used_mafia INTEGER DEFAULT 0,
    last_payout INTEGER
)""")
db.commit()

try:
    c.execute("ALTER TABLE company_options ADD COLUMN min_budget INTEGER DEFAULT 20")
except sqlite3.OperationalError:
    pass
db.commit()

ALLY_NAME_POOL = [
    "یار خفن شرکت", "پسرعموی کلفت", "کارمند بی‌سروپا", "یارِ زیرِ میزی",
    "بادیگارد کیری", "همکار مشکوک", "یار قاچاقی", "کارگر شیفت شب",
]


def find_active_membership(chat_id, uid):
    """آخرین نبرد فعال مافیا/مافیا۲ که این کاربر واقعاً (نه به عنوان یار) توش عضوه رو پیدا می‌کنه.
    خروجی: (kind, battle_id, team) یا None اگه توی هیچ نبردی نباشه."""
    row = c.execute(
        """SELECT mb.id, mm.team FROM mafia_battles mb
           JOIN mafia_members mm ON mm.battle_id=mb.id
           WHERE mb.chat_id=? AND mb.active=1 AND mm.user_id=?
           ORDER BY mb.id DESC LIMIT 1""",
        (chat_id, uid)
    ).fetchone()
    if row:
        return ("mafia", row[0], row[1])
    row = c.execute(
        """SELECT mb.id, mm.team FROM mafia2_battles mb
           JOIN mafia2_members mm ON mm.battle_id=mb.id
           WHERE mb.chat_id=? AND mb.active=1 AND mm.user_id=?
           ORDER BY mb.id DESC LIMIT 1""",
        (chat_id, uid)
    ).fetchone()
    if row:
        return ("mafia2", row[0], row[1])
    return None


def get_round(chat_id):
    row = c.execute("SELECT round_id,status,open_time FROM company_rounds WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        c.execute("INSERT INTO company_rounds(chat_id,round_id,status,open_time) VALUES(?,0,'closed',0)", (chat_id,))
        db.commit()
        return (0, 'closed', 0)
    return row


@dp.message(Command("copen"))
async def company_open(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ فقط ادمین می‌تونه بازار رو باز کنه!")
    round_id, status, open_time = get_round(m.chat.id)
    now = int(time.time())
    if status == 'open':
        return await m.reply("📈 بازار همین الان بازه! اول با /cclose ببندش.")
    new_round = round_id + 1
    names = random.sample(COMPANY_NAME_POOL, 4)
    c.execute("DELETE FROM company_options WHERE chat_id=?", (m.chat.id,))
    c.execute("DELETE FROM company_investments WHERE chat_id=?", (m.chat.id,))
    c.execute("DELETE FROM company_participants WHERE chat_id=?", (m.chat.id,))
    min_budgets = []
    for i, name in enumerate(names, 1):
        mb = random.randint(COMPANY_MIN_BUDGET_LOW, COMPANY_MIN_BUDGET_HIGH)
        min_budgets.append(mb)
        c.execute("INSERT INTO company_options(chat_id,round_id,slot,name,min_budget) VALUES(?,?,?,?,?)", (m.chat.id, new_round, i, name, mb))
    c.execute("UPDATE company_rounds SET round_id=?,status='open',open_time=? WHERE chat_id=?", (new_round, now, m.chat.id))
    db.commit()
    txt = "📈 بازار بورس کیر باز شد!\n\n"
    for i, (name, mb) in enumerate(zip(names, min_budgets), 1):
        txt += f"{i}. {name} (حداقل بودجه: {mb} اسپرم)\n"
    txt += (
        f"\n🧬 هر سرمایه‌گذاری باید حداقل {int(COMPANY_MIN_INVEST_PCT*100)}٪ اسپرمت باشه؛ سقفی نداره!\n"
        f"(نداری؟ با /tosperm سانتت رو تبدیل کن — هر ۱ سانت = {SPERM_RATE} اسپرم)\n\n"
        f"برای سرمایه‌گذاری فقط بنویس /invest (بدون هیچ عددی!)\n"
        f"ربات میاد پیوی خصوصی باهات هماهنگ می‌کنه که کسی نفهمه رو چی و چقدر سرمایه‌گذاری کردی."
    )
    await m.reply(txt)


@dp.message(Command("invest"))
async def company_invest_start(m: Message):
    if m.chat.type == "private":
        return await m.reply("❌ این دستور رو باید توی همون گروهی که بازی می‌کنی بزنی.")
    round_id, status, open_time = get_round(m.chat.id)
    if status != 'open':
        return await m.reply("❌ الان بازاری باز نیست!")
    user(m.chat.id, m.from_user.id, m.from_user.full_name)
    opts = c.execute("SELECT slot,name FROM company_options WHERE chat_id=? AND round_id=? ORDER BY slot", (m.chat.id, round_id)).fetchall()
    if not opts:
        return await m.reply("❌ شرکتی برای این دور پیدا نشد.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{slot}. {name}", callback_data=f"investpick:{m.chat.id}:{round_id}:{slot}")]
        for slot, name in opts
    ])
    tip = ""
    if len(m.text.split()) > 1:
        tip = "\n\n💡 دیگه لازم نیست بعد /invest عدد بنویسی — کل انتخاب رو همینجا با دکمه انجام بده."
    try:
        await m.bot.send_message(
            m.from_user.id,
            "📈 روی کدوم شرکت می‌خوای سرمایه‌گذاری کنی؟\n(این پیام فقط برای خودته)" + tip,
            reply_markup=kb
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        return await m.reply("❌ اول باید ربات رو توی پیوی (چت خصوصی) استارت کنی، بعد دوباره اینجا /invest بزن.")
    await m.reply("📩 برات پیام خصوصی فرستادم! بقیه‌ی سرمایه‌گذاری رو اونجا انجام بده تا لو نره.")


@dp.callback_query(F.data.startswith("investpick:"))
async def company_invest_pick(q: CallbackQuery):
    _, chat_id, round_id, slot = q.data.split(":")
    chat_id, round_id, slot = int(chat_id), int(round_id), int(slot)
    cur_round_id, status, open_time = get_round(chat_id)
    if status != 'open' or cur_round_id != round_id:
        return await q.answer("❌ این دور بازار دیگه بسته یا منقضی شده.", show_alert=True)
    opt = c.execute("SELECT name FROM company_options WHERE chat_id=? AND round_id=? AND slot=?", (chat_id, round_id, slot)).fetchone()
    if not opt:
        return await q.answer("❌ همچین شرکتی پیدا نشد.", show_alert=True)
    pending_invest[q.from_user.id] = {"chat_id": chat_id, "round_id": round_id, "slot": slot, "ts": int(time.time())}
    await q.answer()
    await q.message.edit_text(f"🧬 چند اسپرم می‌خوای روی «{opt[0]}» سرمایه‌گذاری کنی؟\nفقط عددشو بفرست (مثلاً 12).\n(نداری؟ با /tosperm سانتت رو تبدیل کن.)")


@dp.message(F.chat.type == "private", F.text.regexp(r'^\d+$'))
async def company_invest_amount(m: Message):
    pending = pending_invest.get(m.from_user.id)
    if not pending:
        return
    if int(time.time()) - pending["ts"] > PENDING_INVEST_TTL:
        del pending_invest[m.from_user.id]
        return await m.reply("⌛️ زمان انتخابت تموم شد، دوباره از توی گروه /invest بزن.")
    amount = int(m.text)
    chat_id, round_id, slot = pending["chat_id"], pending["round_id"], pending["slot"]
    if amount <= 0:
        return await m.reply("❌ مقدار باید مثبت باشه.")
    cur_round_id, status, open_time = get_round(chat_id)
    if status != 'open' or cur_round_id != round_id:
        del pending_invest[m.from_user.id]
        return await m.reply("❌ بازار اون گروه بسته یا عوض شده، دوباره از توی گروه /invest بزن.")
    opt = c.execute("SELECT name FROM company_options WHERE chat_id=? AND round_id=? AND slot=?", (chat_id, round_id, slot)).fetchone()
    if not opt:
        del pending_invest[m.from_user.id]
        return await m.reply("❌ این شرکت دیگه وجود نداره.")
    sperm = get_sperm(chat_id, m.from_user.id)
    if sperm < amount:
        return await m.reply(f"❌ اسپرم کافی نداری. (موجودی: {sperm})\nبا /tosperm می‌تونی سانتت رو تبدیل کنی.")
    min_required = max(1, int(sperm * COMPANY_MIN_INVEST_PCT))
    if amount < min_required:
        return await m.reply(f"❌ هر سرمایه‌گذاری باید حداقل {int(COMPANY_MIN_INVEST_PCT*100)}٪ اسپرمت باشه!\n📊 حداقل مجاز الان: {min_required} اسپرم")
    part = c.execute("SELECT invested FROM company_participants WHERE chat_id=? AND round_id=? AND user_id=?", (chat_id, round_id, m.from_user.id)).fetchone()
    if not part:
        c.execute("INSERT INTO company_participants(chat_id,round_id,user_id,invested) VALUES(?,?,?,0)", (chat_id, round_id, m.from_user.id))
        db.commit()
    c.execute("UPDATE users SET sperm=sperm-? WHERE chat_id=? AND user_id=?", (amount, chat_id, m.from_user.id))
    c.execute("""INSERT INTO company_investments(chat_id,round_id,slot,user_id,amount) VALUES(?,?,?,?,?)
                 ON CONFLICT(chat_id,round_id,slot,user_id) DO UPDATE SET amount=amount+excluded.amount""",
              (chat_id, round_id, slot, m.from_user.id, amount))
    c.execute("UPDATE company_participants SET invested=invested+? WHERE chat_id=? AND round_id=? AND user_id=?", (amount, chat_id, round_id, m.from_user.id))
    db.commit()
    del pending_invest[m.from_user.id]
    await m.reply("✅ سرمایه‌گذاریت با موفقیت و کاملاً محرمانه ثبت شد!")


@dp.message(Command("cclose"))
async def company_close(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.reply("❌ فقط ادمین می‌تونه بازار رو ببنده!")
    round_id, status, open_time = get_round(m.chat.id)
    if status != 'open':
        return await m.reply("❌ بازاری باز نیست.")
    opts = c.execute("SELECT slot,name,min_budget FROM company_options WHERE chat_id=? AND round_id=?", (m.chat.id, round_id)).fetchall()
    if not opts:
        return await m.reply("❌ شرکتی برای این دور پیدا نشد.")
    totals = {}
    for slot, name, min_budget in opts:
        total = c.execute("SELECT COALESCE(SUM(amount),0) FROM company_investments WHERE chat_id=? AND round_id=? AND slot=?", (m.chat.id, round_id, slot)).fetchone()[0]
        totals[slot] = (name, total, min_budget)

    max_slot = max(totals, key=lambda s: totals[s][1])
    eliminated = {max_slot}
    for slot, (name, total, min_budget) in totals.items():
        if total < min_budget:
            eliminated.add(slot)

    now = int(time.time())
    txt = "📉 نتیجه‌ی بازار بورس!\n\n"
    for slot, (name, total, min_budget) in totals.items():
        if slot in eliminated:
            reason = "گنده شد و ترکید 💥" if slot == max_slot else f"به حداقل بودجه‌ش ({min_budget} اسپرم) نرسید 📉"
            txt += f"❌ {name} ({total} اسپرم) — {reason}\n"
            continue
        top = c.execute(
            "SELECT user_id,amount FROM company_investments WHERE chat_id=? AND round_id=? AND slot=? ORDER BY amount DESC LIMIT 1",
            (m.chat.id, round_id, slot)
        ).fetchone()
        if not top:
            txt += f"⚪️ {name} — کسی سرمایه‌گذاری نکرد، برنده‌ای نداشت\n"
            continue
        winner_id, winner_amount = top
        workers = random.randint(2, 3)
        c.execute(
            "INSERT INTO owned_companies(chat_id,owner_id,name,value,workers,last_payout) VALUES(?,?,?,?,?,?)",
            (m.chat.id, winner_id, name, total, workers, now)
        )
        winner_name = get_name(m.chat.id, winner_id)
        txt += f"👑 {name} ({total} اسپرم) — برنده: {winner_name} (سهم {winner_amount} اسپرم)\n"

    c.execute("UPDATE company_rounds SET status='closed' WHERE chat_id=?", (m.chat.id,))
    db.commit()
    await m.reply(txt)


@dp.message(Command("mycompanies"))
async def my_companies(m: Message):
    rows = c.execute("SELECT id,name,value,workers,workers_bought,workers_used_mafia FROM owned_companies WHERE chat_id=? AND owner_id=?", (m.chat.id, m.from_user.id)).fetchall()
    if not rows:
        return await m.reply("📭 هنوز هیچ شرکتی نداری! وقتی بازار بورس بازه با /invest شرکت کن.")
    txt = "🏢 شرکت‌های تو:\n\n"
    for cid, name, value, workers, bought, used in rows:
        income = int(value * COMPANY_DIVIDEND_PCT)
        available = bought - used
        txt += f"#{cid} {name}\n🧬 ارزش: {value} اسپرم | 📈 سود روزانه: {income} اسپرم\n👥 یار: {bought}/{workers} خریداری‌شده ({available} آماده‌ی استفاده با /useyar)\n\n"
    await m.reply(txt)


# ===== رنک بورس بر اساس ارزش خالص شرکت‌ها =====
BOURSE_RANK_TIERS = [
    (0,    "🥫 دستفروش"),
    (50,   "🏪 مغازه‌دار"),
    (150,  "🏢 تاجر"),
    (400,  "🏦 میلیاردر"),
    (800,  "🚀 ایلان ماسک"),
]

def get_bourse_tier(net_worth):
    tier = BOURSE_RANK_TIERS[0][1]
    for threshold, name in BOURSE_RANK_TIERS:
        if net_worth >= threshold:
            tier = name
    return tier

@dp.message(Command("bourseleader"))
async def bourse_leaderboard(m: Message):
    rows = c.execute(
        "SELECT owner_id, value FROM owned_companies WHERE chat_id=?",
        (m.chat.id,)
    ).fetchall()
    if not rows:
        return await m.reply("📭 هنوز کسی تو بورس این گروه برنده نشده!")
    stats = {}
    for owner_id, value in rows:
        s = stats.setdefault(owner_id, {"net_worth": 0, "companies": 0, "wins": 0, "daily_income": 0})
        s["wins"] += 1
        if value > 0:
            s["net_worth"] += value
            s["companies"] += 1
            s["daily_income"] += int(value * COMPANY_DIVIDEND_PCT)
    leaderboard = sorted(stats.items(), key=lambda kv: kv[1]["net_worth"], reverse=True)[:10]
    txt = "📊 جدول بورس این گروه\n\n"
    for i, (owner_id, s) in enumerate(leaderboard, 1):
        name = get_name(m.chat.id, owner_id)
        tier = get_bourse_tier(s["net_worth"])
        txt += (
            f"{i}. {name}\n"
            f"   {tier}\n"
            f"   🧬 ارزش خالص: {s['net_worth']} اسپرم | 🏢 شرکت‌های فعال: {s['companies']} | 🏆 کل بردها: {s['wins']}\n"
            f"   📈 مجموع سود روزانه: {s['daily_income']} اسپرم\n\n"
        )
    await m.reply(txt)



async def hire_worker(m: Message):
    try:
        cid = int(m.text.split()[1])
    except:
        return await m.reply("Usage: /hire [شماره شرکت] (شماره‌ها رو از /mycompanies ببین)")
    row = c.execute("SELECT owner_id,name,workers,workers_bought FROM owned_companies WHERE id=? AND chat_id=?", (cid, m.chat.id)).fetchone()
    if not row:
        return await m.reply("❌ همچین شرکتی پیدا نشد.")
    owner_id, name, workers, bought = row
    if owner_id != m.from_user.id:
        return await m.reply("❌ این شرکت مال تو نیست!")
    if bought >= workers:
        return await m.reply(f"❌ همه‌ی {workers} یار این شرکت رو قبلاً خریدی!")
    sperm = get_sperm(m.chat.id, m.from_user.id)
    if sperm < COMPANY_WORKER_COST:
        return await m.reply(f"❌ برای خرید یار به {COMPANY_WORKER_COST} اسپرم نیاز داری.")
    c.execute("UPDATE users SET sperm=sperm-? WHERE chat_id=? AND user_id=?", (COMPANY_WORKER_COST, m.chat.id, m.from_user.id))
    c.execute("UPDATE owned_companies SET workers_bought=workers_bought+1 WHERE id=?", (cid,))
    db.commit()
    await m.reply(f"✅ یه یار از «{name}» خریدی! ({bought+1}/{workers})\n🔫 برای فرستادنش به یه نبرد مافیا که توش هستی: /useyar")


@dp.message(Command("useyar"))
async def use_yar(m: Message):
    membership = find_active_membership(m.chat.id, m.from_user.id)
    if not membership:
        return await m.reply("❌ الان توی هیچ نبرد مافیای فعالی نیستی! اول با دکمه‌ی تیم وارد یه نبرد (/mafia یا /mafia2) شو.")
    kind, bid, team = membership
    company = c.execute(
        "SELECT id,name FROM owned_companies WHERE chat_id=? AND owner_id=? AND workers_bought>workers_used_mafia LIMIT 1",
        (m.chat.id, m.from_user.id)
    ).fetchone()
    if not company:
        return await m.reply("❌ هیچ یارِ آماده‌ای نداری! اول با /hire یار بخر.")
    cid, cname = company
    ally_name = random.choice(ALLY_NAME_POOL)
    table = "mafia_members" if kind == "mafia" else "mafia2_members"
    fake_uid = -(cid * 1000000 + random.randint(1, 999999))
    c.execute(f"INSERT INTO {table}(battle_id,user_id,name,team,is_ally) VALUES(?,?,?,?,1)", (bid, fake_uid, ally_name, team))
    c.execute("UPDATE owned_companies SET workers_used_mafia=workers_used_mafia+1 WHERE id=?", (cid,))
    db.commit()
    await m.reply(f"🔫 «{ally_name}» از «{cname}» مخفیانه وارد تیمت شد!\n(این یار سهمی از جایزه نمی‌بره، فقط تعداد تیمت رو زیاد می‌کنه)")


@dp.message(Command("cashout"))
async def cashout_company(m: Message):
    try:
        cid = int(m.text.split()[1])
    except:
        return await m.reply("Usage: /cashout [شماره شرکت] (شماره‌ها رو از /mycompanies ببین)")
    row = c.execute("SELECT owner_id,name,value FROM owned_companies WHERE id=? AND chat_id=?", (cid, m.chat.id)).fetchone()
    if not row:
        return await m.reply("❌ همچین شرکتی پیدا نشد.")
    owner_id, name, value = row
    if owner_id != m.from_user.id:
        return await m.reply("❌ این شرکت مال تو نیست!")
    cashed = value // 2
    if cashed <= 0:
        return await m.reply("❌ ارزش شرکت خیلی کمه که چیزی نقد بشه.")
    c.execute("UPDATE owned_companies SET value=value-? WHERE id=?", (cashed, cid))
    c.execute("UPDATE users SET sperm=sperm+? WHERE chat_id=? AND user_id=?", (cashed, m.chat.id, m.from_user.id))
    db.commit()
    await m.reply(f"🧬 نصف ارزش «{name}» نقد شد!\n💰 {cashed} اسپرم به حسابت اضافه شد.\n📉 ارزش باقی‌مونده‌ی شرکت: {value-cashed} اسپرم\n(برای تبدیل به سانت: /tocent)")


async def company_dividend_loop(bot):
    while True:
        await asyncio.sleep(300)  # هر ۵ دقیقه چک می‌کنه کدوم شرکت باید سود بده
        now = int(time.time())
        day = 24*60*60
        rows = c.execute("SELECT id,chat_id,owner_id,name,value,last_payout FROM owned_companies").fetchall()
        for cid, chat_id, owner_id, name, value, last_payout in rows:
            if now - last_payout < day:
                continue
            if value <= 0:
                c.execute("UPDATE owned_companies SET last_payout=? WHERE id=?", (now, cid))
                db.commit()
                continue
            income = int(value * COMPANY_DIVIDEND_PCT)
            c.execute("UPDATE users SET sperm=sperm+? WHERE chat_id=? AND user_id=?", (income, chat_id, owner_id))
            c.execute("UPDATE owned_companies SET last_payout=? WHERE id=?", (now, cid))
            db.commit()
            try:
                await bot.send_message(owner_id, f"📈 سود روزانه‌ی شرکتت «{name}»!\n🧬 {income} اسپرم به حسابت اضافه شد.")
            except:
                pass
# ================== پایان کمپانی کیر ==================

async def main():
    bot=Bot(TOKEN)
    from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault
    commands = [
        BotCommand(command="help", description="📖 راهنمای کامل بازی"),
        BotCommand(command="grow", description="🌱 رشد کن"),
        BotCommand(command="size", description="📊 اندازه و پروفایل"),
        BotCommand(command="rank", description="🏅 رنک و پیشرفت روزانه"),
        BotCommand(command="ranktop", description="🏆 برترین‌های رنک گروه"),
        BotCommand(command="farm", description="🌾 پنل مزرعه‌ی اسپرم"),
        BotCommand(command="farmrank", description="🌾 رنک مزرعه‌ی من (زنده)"),
        BotCommand(command="farmleader", description="📊 جدول رنک مزرعه‌ی گروه"),
        BotCommand(command="tosperm", description="🧬 تبدیل سانت به اسپرم"),
        BotCommand(command="tocent", description="💰 تبدیل اسپرم به سانت"),
        BotCommand(command="top", description="🏆 جدول بزرگان"),
        BotCommand(command="market", description="🛒 بازار سلبریتی"),
        BotCommand(command="collection", description="📚 کالکشن من"),
        BotCommand(command="spin", description="🎰 اسپین سلبریتی"),
        BotCommand(command="buy", description="🛍 خرید سلبریتی"),
        BotCommand(command="sell", description="💸 فروش سلبریتی"),
        BotCommand(command="list", description="🏪 فروش به دیگران"),
        BotCommand(command="lock", description="🔒 قفل کردن سلبریتی"),
        BotCommand(command="pvp", description="⚔️ دوئل"),
        BotCommand(command="mafia", description="🔫 جنگ مافیا تیمی"),
        BotCommand(command="mafia2", description="🔫 مافیا (فاش‌شدن تیم‌ها در پایان)"),
        BotCommand(command="loan", description="💰 وام دادن"),
        BotCommand(command="repay", description="✅ پرداخت بدهی"),
        BotCommand(command="collectors", description="🏆 بهترین کلکسیونرها"),
        BotCommand(command="gloan", description="🏦 وام گرفتن از بازی"),
        BotCommand(command="gpay", description="✅ پرداخت وام بازی"),
        BotCommand(command="copen", description="📈 باز کردن بازار کمپانی (ادمین)"),
        BotCommand(command="invest", description="💰 سرمایه‌گذاری روی یه کمپانی"),
        BotCommand(command="cclose", description="📉 بستن بازار و اعلام برنده (ادمین)"),
        BotCommand(command="mycompanies", description="🏢 کمپانی‌های من"),
        BotCommand(command="bourseleader", description="📊 جدول رنک بورس گروه"),
        BotCommand(command="hire", description="🔫 خرید یار برای کمپانی"),
        BotCommand(command="useyar", description="🕵️ فرستادن یار به نبرد مافیای فعلیت"),
        BotCommand(command="cashout", description="💸 نقد کردن نصف ارزش کمپانی"),
        BotCommand(command="addcm", description="➕ افزودن سانت به کاربر (ادمین)"),
        BotCommand(command="luckydrop", description="🎉 دادن سانت به یه بازیکن رندوم (ادمین)"),
        BotCommand(command="resetgroup", description="⚠️ پاک کردن کامل داده‌های این گروه (ادمین)"),
        BotCommand(command="addsperm", description="➕ افزودن اسپرم به کاربر (ادمین)"),
        BotCommand(command="addspermall", description="➕ افزودن اسپرم به همه (ادمین)"),
        BotCommand(command="addcb", description="👑 دادن/گرفتن سلبریتی از کاربر (ادمین)"),
        BotCommand(command="getfileid", description="🆔 گرفتن file_id عکس (ادمین)"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
    asyncio.create_task(check_loans(bot))
    asyncio.create_task(company_dividend_loop(bot))
    await dp.start_polling(bot)

if __name__=="__main__":
    import asyncio
    asyncio.run(main())
