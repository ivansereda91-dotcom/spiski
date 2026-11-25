"""Семейные списки: покупки и дела, общие для двоих (aiogram 3)."""
import asyncio
import html
import logging
import os
import re
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, KeyboardButton, Message,
                           ReplyKeyboardMarkup, TelegramObject)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").replace(" ", "").split(",") if x}
TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Paris"))
MORNING_AT = dtime(*map(int, os.getenv("MORNING_AT", "08:00").split(":")))
MAX_SHOWN = 80  # у Telegram ограничение на число кнопок в сообщении

TITLE = {"buy": "🛒 Покупки", "todo": "✅ Дела"}
WHERE = {"buy": "в покупки", "todo": "в дела"}
ASK = {"buy": "Что купить? Можно сразу несколько: каждое с новой строки или через запятую.",
       "todo": "Какое дело добавить? Несколько дел — каждое с новой строки."}

B_BUY, B_TODO, B_ADD_BUY, B_ADD_TODO = "🛒 Покупки", "✅ Дела", "➕ Купить", "➕ Дело"
MENU_TEXTS = {B_BUY, B_TODO, B_ADD_BUY, B_ADD_TODO}

MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=B_BUY), KeyboardButton(text=B_TODO)],
              [KeyboardButton(text=B_ADD_BUY), KeyboardButton(text=B_ADD_TODO)]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Напиши, что добавить",
)

router = Router()
e = html.escape


class Add(StatesGroup):
    text = State()


class Access(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        u = data.get("event_from_user")
        if u is None:
            return
        if u.id not in ALLOWED:
            if isinstance(event, Message):
                await event.answer(f"Это частный бот.\nТвой ID: <code>{u.id}</code>\n"
                                   "Перешли его хозяину, чтобы получить доступ.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа")
            return
        db.touch_user(u.id, u.first_name or str(u.id))
        return await handler(event, data)


# ---------- разбор текста ----------

def split_items(lst: str, text: str) -> list[str]:
    """Покупки делим по строкам и запятым, дела — только по строкам (в делах запятые бывают внутри)."""
    parts = text.splitlines()
    if lst == "buy":
        parts = [p for line in parts for p in re.split(r"[,;]", line)]
    return [re.sub(r"^\s*[-•*–—]\s*", "", p).strip() for p in parts if p.strip()]


# ---------- отображение списка ----------

def list_text(lst: str, mode: str) -> str:
    left, done = db.counts(lst)
    if left == 0 and done == 0:
        return f"<b>{TITLE[lst]}</b>\n\nСписок пуст. Нажми «➕ Добавить» или просто напиши, что нужно."
    word = "куплено" if lst == "buy" else "сделано"
    t = f"<b>{TITLE[lst]}</b> · осталось {left}" + (f", {word} {done}" if done else "")
    if mode == "del":
        t += "\n\n🗑 Нажми на пункт, чтобы удалить его совсем."
    else:
        t += "\n\nНажми на пункт, чтобы поставить или снять галочку."
    if left + done > MAX_SHOWN:
        t += f"\n\nПоказаны первые {MAX_SHOWN}. Убери отмеченные, чтобы увидеть остальные."
    return t


def list_kb(lst: str, mode: str):
    kb = InlineKeyboardBuilder()
    rows = db.items(lst)[:MAX_SHOWN]
    for r in rows:
        if mode == "del":
            kb.button(text=f"🗑 {r['text']}", callback_data=f"x:{r['id']}")
        else:
            kb.button(text=f"{'✅' if r['done'] else '⬜'} {r['text']}", callback_data=f"t:{r['id']}")
    kb.adjust(1)
    if mode == "del":
        kb.row(InlineKeyboardButton(text="↩️ Готово", callback_data=f"v:{lst}:n"))
        return kb.as_markup()
    extra = InlineKeyboardBuilder()
    extra.button(text="➕ Добавить", callback_data=f"a:{lst}")
    if any(r["done"] for r in rows):
        extra.button(text="🧹 Убрать отмеченные", callback_data=f"c:{lst}")
    kb.row(*extra.buttons)
    tail = InlineKeyboardBuilder()
    if rows:
        tail.button(text="🗑 Удалить", callback_data=f"v:{lst}:del")
    tail.button(text="🔄 Обновить", callback_data=f"v:{lst}:n")
    kb.row(*tail.buttons)
    return kb.as_markup()


async def show_list(msg: Message, lst: str):
    await msg.answer(list_text(lst, "n"), reply_markup=list_kb(lst, "n"))


async def refresh(cb: CallbackQuery, lst: str, mode: str = "n"):
    try:
        await cb.message.edit_text(list_text(lst, mode), reply_markup=list_kb(lst, mode))
    except TelegramBadRequest as ex:
        if "not modified" not in str(ex):
            raise


# ---------- добавление ----------

async def do_add(msg: Message, bot: Bot, lst: str, text: str):
    wanted = split_items(lst, text)
    added = db.add(lst, wanted, msg.chat.id)
    if not added:
        await msg.answer("Это уже есть в списке.", reply_markup=MENU)
        return
    low = {a.lower() for a in added}
    dup = [w for w in wanted if " ".join(w.split())[:100].lower() not in low]
    reply = f"Добавил {WHERE[lst]}: " + ", ".join(e(a) for a in added)
    if dup:
        reply += "\nУже было в списке: " + ", ".join(e(d) for d in dup)
    await msg.answer(reply, reply_markup=MENU)
    await show_list(msg, lst)
    who = e(db.name(msg.chat.id))
    note = f"{TITLE[lst][:1]} <b>{who}</b> добавил(а) {WHERE[lst]}:\n" + "\n".join(f"• {e(a)}" for a in added)
    for uid in ALLOWED - {msg.chat.id}:
        if db.notify_on(uid):
            try:
                await bot.send_message(uid, note)
            except Exception as ex:  # noqa: BLE001 — второй ещё не нажал Start или заблокировал бота
                logging.info("Не отправил уведомление %s: %s", uid, ex)


@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "Привет! Здесь два общих списка — <b>покупки</b> и <b>дела</b>.\n\n"
        "• <b>➕ Купить</b> / <b>➕ Дело</b> — добавить, можно сразу несколько.\n"
        "• <b>🛒 Покупки</b> / <b>✅ Дела</b> — открыть список и отмечать галочкой.\n"
        "• Или просто напиши текст — я спрошу, в какой список.\n\n"
        "Когда один из вас что-то добавляет, второму приходит уведомление. Выключить: /notify\n"
        f"Каждое утро в {MORNING_AT:%H:%M} пришлю список дел. Выключить: /morning",
        reply_markup=MENU)


@router.message(Command("notify"))
async def notify_cmd(msg: Message):
    on = db.toggle_notify(msg.chat.id)
    await msg.answer("🔔 Уведомления включены." if on else "🔕 Уведомления выключены. Включить снова: /notify",
                     reply_markup=MENU)


@router.message(Command("morning"))
async def morning_cmd(msg: Message):
    on = db.toggle_morning(msg.chat.id)
    await msg.answer(f"☀️ Утренний список дел включён — пришлю в {MORNING_AT:%H:%M}." if on
                     else "🌙 Утренний список дел выключен. Включить снова: /morning", reply_markup=MENU)


@router.message(Command("id"))
async def my_id(msg: Message):
    await msg.answer(f"Твой ID: <code>{msg.chat.id}</code>")


@router.message(F.text == B_BUY)
async def open_buy(msg: Message, state: FSMContext):
    await state.clear()
    await show_list(msg, "buy")


@router.message(F.text == B_TODO)
async def open_todo(msg: Message, state: FSMContext):
    await state.clear()
    await show_list(msg, "todo")


@router.message(F.text.in_({B_ADD_BUY, B_ADD_TODO}))
async def ask_add(msg: Message, state: FSMContext):
    lst = "buy" if msg.text == B_ADD_BUY else "todo"
    await state.set_state(Add.text)
    await state.update_data(lst=lst)
    await msg.answer(ASK[lst], reply_markup=MENU)


@router.message(StateFilter(Add.text), F.text, ~F.text.in_(MENU_TEXTS), ~F.text.startswith("/"))
async def got_add(msg: Message, state: FSMContext, bot: Bot):
    lst = (await state.get_data()).get("lst", "buy")
    await state.clear()
    await do_add(msg, bot, lst, msg.text)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_text(msg: Message, state: FSMContext):
    await state.update_data(pending=msg.text)
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 В покупки", callback_data="p:buy")
    kb.button(text="✅ В дела", callback_data="p:todo")
    await msg.answer(f"Куда добавить?\n<i>{e(msg.text[:300])}</i>", reply_markup=kb.as_markup())


@router.message()
async def other(msg: Message):
    await msg.answer("Я понимаю только текст. Нажми кнопку внизу или напиши, что добавить.", reply_markup=MENU)


# ---------- кнопки под списком ----------

@router.callback_query(F.data.startswith("p:"))
async def cb_pending(cb: CallbackQuery, state: FSMContext, bot: Bot):
    lst = cb.data[2:]
    text = (await state.get_data()).get("pending")
    await state.update_data(pending=None)
    await cb.answer()
    if not text:
        await cb.message.edit_text("Этот текст уже добавлен или устарел. Напиши ещё раз.")
        return
    await cb.message.delete()
    await do_add(cb.message, bot, lst, text)


@router.callback_query(F.data.startswith("t:"))
async def cb_toggle(cb: CallbackQuery):
    res = db.toggle(int(cb.data[2:]), cb.from_user.id)
    if res is None:
        await cb.answer("Этого пункта уже нет")
        lst = "buy" if TITLE["buy"] in (cb.message.text or "") else "todo"
        await refresh(cb, lst)
        return
    lst, done = res
    await cb.answer("✅ Отмечено" if done else "Галочка снята")
    await refresh(cb, lst)


@router.callback_query(F.data.startswith("x:"))
async def cb_delete(cb: CallbackQuery):
    lst = db.delete(int(cb.data[2:]))
    await cb.answer("Удалено" if lst else "Этого пункта уже нет")
    if lst is None:
        lst = "buy" if TITLE["buy"] in (cb.message.text or "") else "todo"
    await refresh(cb, lst, "del" if db.items(lst) else "n")


@router.callback_query(F.data.startswith("c:"))
async def cb_clear(cb: CallbackQuery):
    lst = cb.data[2:]
    n = db.clear_done(lst)
    await cb.answer(f"Убрано: {n}")
    await refresh(cb, lst)


@router.callback_query(F.data.startswith("v:"))
async def cb_view(cb: CallbackQuery):
    _, lst, mode = cb.data.split(":")
    await cb.answer()
    await refresh(cb, lst, mode)


@router.callback_query(F.data.startswith("a:"))
async def cb_add(cb: CallbackQuery, state: FSMContext):
    lst = cb.data[2:]
    await state.set_state(Add.text)
    await state.update_data(lst=lst)
    await cb.answer()
    await cb.message.answer(ASK[lst], reply_markup=MENU)


# ---------- утренняя рассылка дел ----------

async def send_morning(bot: Bot):
    left, _ = db.counts("todo")
    if left == 0:
        logging.info("Утро: дел нет, ничего не шлю")
        return
    text = "☀️ <b>Доброе утро!</b> Вот дела на сегодня.\n\n" + list_text("todo", "n")
    for uid in ALLOWED:
        if not db.morning_on(uid):
            continue
        try:
            await bot.send_message(uid, text, reply_markup=list_kb("todo", "n"))
        except Exception as ex:  # noqa: BLE001
            logging.info("Не отправил утренний список %s: %s", uid, ex)


async def morning_loop(bot: Bot):
    """Раз в день в MORNING_AT по Ницце. Дату отправки храним в базе, чтобы после перезапуска не слать дважды."""
    while True:
        now = datetime.now(TZ)
        today = now.date().isoformat()
        if now.time() >= MORNING_AT and db.meta_get("morning_sent") != today:
            # если бот лежал в 8:00 и поднялся позже — пришлём, но не позже полудня
            if now.time() < dtime(12, 0):
                await send_morning(bot)
            db.meta_set("morning_sent", today)
        target = datetime.combine(now.date(), MORNING_AT, TZ)
        if now >= target:
            target = datetime.combine(now.date() + timedelta(days=1), MORNING_AT, TZ)
        await asyncio.sleep(min(max((target - datetime.now(TZ)).total_seconds(), 1), 600))


async def main():
    db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(Access())
    dp.callback_query.outer_middleware(Access())
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Меню и подсказка"),
                               BotCommand(command="notify", description="Вкл/выкл уведомления"),
                               BotCommand(command="morning", description="Вкл/выкл утренний список дел"),
                               BotCommand(command="id", description="Мой ID")])
    asyncio.create_task(morning_loop(bot))
    logging.info("Списки запущены, доступ: %s", sorted(ALLOWED))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
