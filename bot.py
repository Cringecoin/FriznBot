# bot_closed_invite_stats.py
# Требования: aiogram==3.x, aiosqlite
# pip install aiogram aiosqlite

import asyncio
import aiosqlite
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated

# ========== НАСТРОЙКИ ==========
TOKEN = "8413495032:AAETPtCC90sj6NOMdZcchoxgNCJsm5d2ehI"
ADMIN_IDS = {1369669762}
DB_PATH = "bot_closed_invite_stats.db"
# ================================

from aiogram.client.default import DefaultBotProperties

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ---------- Время ----------
def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def period_bounds(period: str) -> Tuple[int, int]:
    to_ts = now_ts()
    now = datetime.now(timezone.utc)
    if period == "today":
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    elif period == "week":
        start = (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
    elif period == "month":
        start = (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
    else:
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (int(start.timestamp()), to_ts)


# ---------- БД ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE NOT NULL,
            username TEXT,
            title TEXT,
            invite_link TEXT,
            created_at INTEGER NOT NULL
        )
        """
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS joins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            channel_chat_id TEXT NOT NULL,
            ts INTEGER NOT NULL,
            via_invite_link TEXT
        )
        """
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            channel_chat_id TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
        """
        )
        await db.commit()


# ---------- Утилиты работы с каналами ----------
async def add_channel(
    identifier: str, invite_link: Optional[str] = None
) -> Tuple[bool, str]:
    try:
        chat = await bot.get_chat(identifier)
    except Exception as e:
        return False, f"Не удалось получить информацию о чате: {e}"

    chat_id = str(chat.id)
    username = chat.username or ""
    title = chat.title or username or chat_id
    created = now_ts()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO channels (chat_id, username, title, invite_link, created_at)
            VALUES (?, ?, ?, ?, ?)
        """,
            (chat_id, username, title, invite_link, created),
        )
        await db.commit()
    return (
        True,
        f"Канал добавлен/обновлён: <b>{title}</b> (@{username or 'нет'}) — invite: {invite_link or 'нет'}",
    )


async def remove_channel(identifier: str) -> Tuple[bool, str]:
    try:
        chat = await bot.get_chat(identifier)
    except Exception as e:
        return False, f"Не удалось получить информацию о чате: {e}"
    chat_id = str(chat.id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
        await db.commit()
        if cur.rowcount:
            return True, "Канал удалён из мониторинга."
        else:
            return False, "Канал не найден в базе."


async def list_channels_db() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT chat_id, username, title, invite_link FROM channels ORDER BY id"
        )
        rows = await cur.fetchall()
        return rows


# ---------- Запись joins/leaves ----------
async def record_join(
    user: types.User, channel_chat_id: str, via_invite_link: Optional[str]
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO joins (user_id, username, channel_chat_id, ts, via_invite_link)
            VALUES (?, ?, ?, ?, ?)
        """,
            (user.id, user.username or "", channel_chat_id, now_ts(), via_invite_link),
        )
        await db.commit()


async def record_leave(user: types.User, channel_chat_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO leaves (user_id, username, channel_chat_id, ts)
            VALUES (?, ?, ?, ?)
        """,
            (user.id, user.username or "", channel_chat_id, now_ts()),
        )
        await db.commit()


# ---------- Подсчёты ----------
async def count_joins(channel_chat_id: str, period: str) -> int:
    ts_from, ts_to = period_bounds(period)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) FROM joins
            WHERE channel_chat_id = ? AND ts BETWEEN ? AND ?
        """,
            (channel_chat_id, ts_from, ts_to),
        )
        r = await cur.fetchone()
        return r[0] if r else 0


async def count_leaves(channel_chat_id: str, period: str) -> int:
    ts_from, ts_to = period_bounds(period)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) FROM leaves
            WHERE channel_chat_id = ? AND ts BETWEEN ? AND ?
        """,
            (channel_chat_id, ts_from, ts_to),
        )
        r = await cur.fetchone()
        return r[0] if r else 0


async def count_total(channel_chat_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM joins WHERE channel_chat_id = ?", (channel_chat_id,)
        )
        r = await cur.fetchone()
        return r[0] if r else 0


# ---------- Рендер списка ----------
async def render_stats_list(period: str) -> str:
    rows = await list_channels_db()
    if not rows:
        return "Список каналов пуст. Добавь канал командой /addchannel"

    period_labels = {
        "today": "сегодня",
        "week": "неделя",
        "month": "месяц",
        "all": "всё время",
    }
    label = period_labels.get(period, period)

    lines = [f"Статистика — <b>{label}</b>\n"]
    idx = 1
    for chat_id, username, title, invite_link in rows:
        display = title or username or chat_id
        if username:
            link = f"https://t.me/{username}"
        elif invite_link:
            link = invite_link
        else:
            link = (
                f"https the://t.me/c/{chat_id[4:]}"
                if chat_id.startswith("-100")
                else f"t.me/joinchat/{chat_id}"
            )

        # Подписки
        j_today = await count_joins(chat_id, "today")
        j_week = await count_joins(chat_id, "week")
        j_month = await count_joins(chat_id, "month")
        j_total = await count_total(chat_id)

        # Отписки
        l_today = await count_leaves(chat_id, "today")
        l_week = await count_leaves(chat_id, "week")
        l_month = await count_leaves(chat_id, "month")
        l_total = await count_leaves(chat_id, "all")

        lines.append(
            f"{idx}. <b>{display}</b> — <a href='{link}'>ссылка</a>\n"
            f"    Подписки: {j_today} сегодня / {j_week} за неделю / {j_month} за месяц / {j_total} всего\n"
            f"    Отписки: {l_today} сегодня / {l_week} за неделю / {l_month} за месяц / {l_total} всего\n"
        )
        idx += 1
    return "\n".join(lines)


# ---------- Хэндлеры команд ----------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    user = message.from_user

    # === Сообщение 1 ===
    msg1 = (
        f"👋 Привет, {user.first_name}\n\n"
        "<b>Прорекламируй нас и получи подарок за 400⭐️, осталось 58🍭  штучек!</b>\n\n"
        "Доказательства, что мы работаем честно и быстро⚡️ — @FriznBot_otzv❤️"
    )
    await message.answer(msg1)

    # === Сообщение 2 ===
    msg2 = (
        "1️⃣ Напиши «спасибо» и поставь лайк под комментарием с которого узнал о нас!\n\n"
        "2️⃣ Напиши под 10 любых видео\n"
        "፰፪፪፯፪፱፪፯፱፰፱፪፪፯፪፱፪፯፱፰፱፪፪፯፰፱፪፪፯፪\n"
        "Не говорите ему что\n"
        "@FriznBot ваще всем тортик дарит😏\n"
        "፰፪፪፯፪፱፪፯፱፰፱፪፪፯፪፱፪፯፱፰፱፪፪፯፰፱፪፪፯፪\n\n"
        "<b>Обязательно лайкни все свои комменты</b>"
    )
    await message.answer(msg2)

    # === Сообщение 3 ===
    msg3 = "<b>🤖 Когда будет готово, скинь боту скрины — система отправит подарок!</b>"
    await message.answer(msg3)


@dp.message(Command(commands=["addchannel"]))
async def cmd_addchannel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("У тебя нет прав для добавления каналов.")
        return

    if not message.text or len(message.text.split()) < 2:
        await message.reply(
            "Использование:\n"
            "/addchannel @username [invite_link]\n"
            "/addchannel -1001234567890 https://t.me/+abc\n\n"
            "Пример:\n"
            "/addchannel @ton_scientist"
        )
        return

    args = message.text.split(maxsplit=1)[1]
    parts = args.split(maxsplit=1)
    identifier = parts[0]
    invite_link = parts[1] if len(parts) > 1 else None

    if identifier.startswith("@"):
        pass
    elif identifier.lstrip("-").isdigit():
        num = identifier.lstrip("-")
        if len(num) >= 10:
            identifier = (
                "-100" + num if not identifier.startswith("-100") else identifier
            )
        else:
            identifier = "@" + identifier
    else:
        identifier = "@" + identifier

    success, response = await add_channel(identifier, invite_link)
    await message.reply(response)


@dp.message(Command(commands=["removechannel"]))
async def cmd_removechannel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("У тебя нет прав.")
        return

    if not message.text or len(message.text.split()) < 2:
        await message.reply("Использование: /removechannel @username или -100...")
        return

    identifier = message.text.split(maxsplit=1)[1]
    success, resp = await remove_channel(identifier)
    await message.reply(resp)


@dp.message(Command(commands=["listchannels"]))
async def cmd_listchannels(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("Доступ только админам.")
        return
    rows = await list_channels_db()
    if not rows:
        await message.reply("Каналы не добавлены.")
        return
    text = "Список каналов:\n\n"
    for i, (chat_id, username, title, _) in enumerate(rows, 1):
        text += f"{i}. <b>{title or username or chat_id}</b> — <code>{chat_id}</code>\n"
    await message.reply(text)


@dp.message(Command(commands=["admin"]))
async def cmd_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("Доступ только для админов.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="stats_today"),
                InlineKeyboardButton(text="Неделя", callback_data="stats_week"),
            ],
            [
                InlineKeyboardButton(text="Месяц", callback_data="stats_month"),
                InlineKeyboardButton(text="Всё", callback_data="stats_all"),
            ],
        ]
    )
    await message.reply("Выбери период статистики:", reply_markup=kb)


@dp.callback_query(lambda c: c.data and c.data.startswith("stats_"))
async def stats_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    period = call.data.split("_", 1)[1]
    text = await render_stats_list(period)
    await call.message.edit_text(text, disable_web_page_preview=True)


# ---------- Фото / подарок flow ----------
@dp.message(lambda m: m.photo)
async def on_photo(message: types.Message):
    await message.answer("<b>⌛️ Выбираем подарок...</b>")
    await asyncio.sleep(1.2)
    await message.answer("<b>⌛️ Ещё чуть-чуть...</b>")
    await asyncio.sleep(1.2)
    await message.answer("<b>⚡️ Почти готово...</b>")
    await asyncio.sleep(1.2)

    gift_link = "https://t.me/nft/SnoopDogg-376902"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 ЗАБРАТЬ ПОДАРОК", callback_data="get_gift")]
        ]
    )
    await message.answer(
        f"<b>✅ Успешно, вы успели!</b>\nВаш подарок:\n{gift_link}", reply_markup=kb
    )


# ---------- Подарок: список всех каналов + одна кнопка ----------
@dp.callback_query(lambda c: c.data == "get_gift")
async def on_get_gift(call: types.CallbackQuery):
    rows = await list_channels_db()
    if not rows:
        await call.message.answer("Пока нет спонсоров. Админ должен добавить каналы.")
        return

    lines = [
        "3️⃣ и последнее, чтобы забрать подарок подпишись на спонсоров!\n",
        "👮‍♀️ Добавь их в архив и выключи звук, чтобы не мешали!\n",
    ]

    for idx, (chat_id, username, title, invite_link) in enumerate(rows, 1):
        display = title or username or "Канал"
        if username:
            link = f"https://t.me/{username}"
        elif invite_link:
            link = invite_link
        else:
            link = (
                f"https://t.me/c/{chat_id[4:]}"
                if chat_id.startswith("-100")
                else f"t.me/joinchat/{chat_id}"
            )
        lines.append(f"{idx}. <a href='{link}'>{display}</a>")

    lines.append("\n<b>После подписки на ВСЕ нажми:</b>")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ВЫПОЛНЕНО", callback_data="check_all")]
        ]
    )

    await call.message.edit_text(
        "\n".join(lines), reply_markup=kb, disable_web_page_preview=True
    )


# ---------- Проверка всех каналов сразу ----------
@dp.callback_query(lambda c: c.data == "check_all")
async def on_check_all(call: types.CallbackQuery):
    rows = await list_channels_db()
    if not rows:
        await call.answer("Нет каналов.", show_alert=True)
        return

    not_subscribed = []

    for idx, (chat_id, username, title, invite_link) in enumerate(rows, 1):
        try:
            member = await bot.get_chat_member(
                chat_id=chat_id, user_id=call.from_user.id
            )
            if member.status not in ("member", "administrator", "creator"):
                display = title or username or "Канал"
                if username:
                    link = f"https://t.me/{username}"
                elif invite_link:
                    link = invite_link
                else:
                    link = f"https://t.me/c/{chat_id[4:]}"
                not_subscribed.append(f"{idx}. <a href='{link}'>{display}</a>")
        except Exception as e:
            not_subscribed.append(f"{idx}. Ошибка: {e}")

    if not_subscribed:
        text = "Вы не подписаны на:\n" + "\n".join(not_subscribed)
        await call.message.reply(text, disable_web_page_preview=True)
        await call.answer("Подпишитесь на все каналы!", show_alert=True)
    else:
        await call.message.edit_text(
            "🔥 Всё готово! Вы подписались на все каналы — напишите менеджеру для получения подарка!",
            reply_markup=None,
        )


# ---------- Отслеживание присоединений/отписок ----------
@dp.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated):
    try:
        old = event.old_chat_member
        new = event.new_chat_member
        chat = event.chat
        user = event.from_user or (new.user if new else None)
    except:
        return

    if not chat or not user:
        return

    chat_id_str = str(chat.id)
    old_status = getattr(old, "status", None)
    new_status = getattr(new, "status", None)

    if old_status in ("left", "kicked", None) and new_status in (
        "member",
        "administrator",
        "creator",
    ):
        via_link = None
        invite_obj = getattr(event, "invite_link", None)
        if invite_obj:
            via_link = getattr(invite_obj, "invite_link", None) or getattr(
                invite_obj, "link", None
            )
            if via_link:
                via_link = str(via_link)
        await record_join(user, chat_id_str, via_link)

    if old_status in ("member", "administrator", "creator") and new_status in (
        "left",
        "kicked",
    ):
        await record_leave(user, chat_id_str)
        try:
            await bot.send_message(
                user.id, f"Вы отписались от канала {chat.title or ''}"
            )
        except:
            pass


# ---------- Запуск ----------
async def main():
    await init_db()  # ← ВАЖНО: теперь БД создаётся!
    print("Бот запущен Убедись, что бот — админ во всех каналах!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлен вручную.")
