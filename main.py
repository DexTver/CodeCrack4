import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, BadRequest

# ---------------------------------------------------------------------------
# 1.  Настройки и рекорды
# ---------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Добавьте BOT_TOKEN в .env")

CODE_LENGTH = 4
RECORD_FILE = Path("records.json")


def load_records() -> dict[int, dict[str, int]]:
    """Формат: {user_id: {"easy": best, "hard": best}}"""
    if RECORD_FILE.exists():
        try:
            raw = json.loads(RECORD_FILE.read_text("utf‑8"))
            return {int(u): {m: int(v) for m, v in d.items()} for u, d in raw.items()}
        except Exception as e:
            logging.warning("records.json повреждён: %s", e)
    return {}


def save_records(records):
    tmp = RECORD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False), "utf‑8")
    tmp.replace(RECORD_FILE)


RECORDS = load_records()

# ---------------------------------------------------------------------------
# 2.  helpers
# ---------------------------------------------------------------------------

def generate_secret():
    return "".join(str(random.randint(0, 9)) for _ in range(CODE_LENGTH))


def make_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🎮 Easy", "😎 Hard"], ["🏆 Рекорд"]], resize_keyboard=True
    )


def pattern_for_guess(secret: str, guess: str) -> str:
    return "".join(g if g == s else "*" for g, s in zip(guess, secret))

# ---------------------------------------------------------------------------
# 3.  После перезапуска
# ---------------------------------------------------------------------------
BOOT_TIME = datetime.now(timezone.utc)
APOLOGIZED_USERS: set[int] = set()


def is_old(update: Update) -> bool:
    msg_time = update.message.date
    if msg_time.tzinfo is None:
        msg_time = msg_time.replace(tzinfo=timezone.utc)
    return msg_time < BOOT_TIME

# ---------------------------------------------------------------------------
# 4.  Команды
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Код‑Мастер 4* — угадай 4‑значный код.\n\n"
        "*Easy* — угаданные цифры раскрываются навсегда.\n"
        "*Hard* — результат виден только для текущей попытки.\n\n"
        "Выберите режим кнопкой или пришлите 4 цифры.",
        parse_mode="Markdown",
        reply_markup=make_keyboard(),
    )


async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    context.user_data.clear()
    context.user_data.update(
        {
            "secret": generate_secret(),
            "attempts": 0,
            "mode": mode,           # "easy" | "hard"
            "revealed": ["*"] * CODE_LENGTH,  # только для easy
        }
    )
    await update.message.reply_text(
        f"🎲 Новая игра *{mode.title()}*. Я загадал код!",
        parse_mode="Markdown",
        reply_markup=make_keyboard(),
    )


async def record_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rec = RECORDS.get(uid, {})
    msg = []
    for mode in ("easy", "hard"):
        val = rec.get(mode)
        msg.append(f"{mode.title()}: {val if val else '—'}")
    await update.message.reply_text("Ваши рекорды:\n" + " | ".join(msg), reply_markup=make_keyboard())

# ---------------------------------------------------------------------------
# 5.  Игровой поток
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # 5.1 Сообщения во время простоя
    if is_old(update):
        if uid not in APOLOGIZED_USERS:
            await update.message.reply_text(
                "Извините, бот был недоступен. Сейчас снова онлайн!",
                reply_markup=make_keyboard(),
            )
            APOLOGIZED_USERS.add(uid)
        return

    text = update.message.text.strip()

    # 5.2 Выбор режима кнопкой
    if text in {"🎮 Easy", "😎 Hard"}:
        mode = "easy" if "Easy" in text else "hard"
        await new_game(update, context, mode)
        return
    if text == "🏆 Рекорд":
        await record_cmd(update, context)
        return

    # 5.3 Проверка на код
    if not text.isdigit() or len(text) != CODE_LENGTH:
        await update.message.reply_text(f"Введите ровно {CODE_LENGTH} цифры.", reply_markup=make_keyboard())
        return

    # 5.4 Если игра ещё не начата — по умолчанию Easy
    if "secret" not in context.user_data:
        await new_game(update, context, "easy")
    mode = context.user_data["mode"]
    secret = context.user_data["secret"]
    context.user_data["attempts"] += 1

    # 5.5  HARD: удаляем старую пару сообщений
    if mode == "hard":
        for key in ("last_user_msg", "last_bot_msg"):
            mid = context.user_data.get(key)
            if mid:
                try:
                    await update.effective_chat.delete_message(mid)
                except BadRequest:
                    pass  # сообщение уже удалено / старше 48h
        context.user_data["last_user_msg"] = update.message.message_id

    # 5.6 Формируем ответ
    if mode == "easy":
        for i, (g, s) in enumerate(zip(text, secret)):
            if g == s:
                context.user_data["revealed"][i] = g
        pattern = "".join(context.user_data["revealed"])
    else:  # hard
        pattern = pattern_for_guess(secret, text)

    bot_msg = await update.message.reply_text(
        f"Результат: `{pattern}`", parse_mode="Markdown"
    )
    if mode == "hard":
        context.user_data["last_bot_msg"] = bot_msg.message_id

    # 5.7  Победа
    if text == secret:
        attempts = context.user_data["attempts"]
        best = RECORDS.get(uid, {}).get(mode)
        new_best = best is None or attempts < best
        if new_best:
            RECORDS.setdefault(uid, {})[mode] = attempts
            save_records(RECORDS)
        await update.message.reply_text(
            f"🎉 *{mode.title()}* победа! Код `{secret}` угадан за *{attempts}* попыток." +
            ("\n🏆 Новый рекорд!" if new_best else "") +
            "\nВыберите режим, чтобы сыграть снова.",
            parse_mode="Markdown",
            reply_markup=make_keyboard(),
        )
        context.user_data.clear()

# ---------------------------------------------------------------------------
# 6.  Запуск приложения
# ---------------------------------------------------------------------------

def create_app():
    req = HTTPXRequest(connect_timeout=10.0, read_timeout=15.0, write_timeout=15.0)
    return ApplicationBuilder().token(BOT_TOKEN).request(req).build()


def main():
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

    while True:
        app = create_app()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("record", record_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        try:
            print("Bot is running… Press Ctrl‑C to stop.")
            app.run_polling(stop_signals=None)
        except NetworkError as e:
            logging.warning("NetworkError: %s — перезапуск через 5 с", e)
            time.sleep(5)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
