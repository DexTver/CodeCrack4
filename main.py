#!/usr/bin/env python3
"""
Telegram‑бот «Код‑Мастер 4» — угадай 4‑значный секретный код.

📦  Новое: *персистентные рекорды* — лучшие результаты игроков хранятся в файле
     `records.json` и переживают перезапуски бота.

• `.env`  —  BOT_TOKEN=…
• Зависимости:  `python-telegram-bot>=20`  `python-dotenv`
"""

import json
import logging
import os
import random
import time
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
from telegram.error import NetworkError

# ---------------------------------------------------------------------------
# 1. Настройки и инициализация
# ---------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Переменная BOT_TOKEN не задана (см. .env)")

CODE_LENGTH = 4
RECORD_FILE = Path("records.json")


def load_records() -> dict[int, int]:
    """Читает JSON‑файл рекордов. Формат: {user_id(str): attempts(int)}"""
    if RECORD_FILE.exists():
        try:
            data = json.loads(RECORD_FILE.read_text("utf‑8"))
            return {int(uid): int(best) for uid, best in data.items()}
        except (json.JSONDecodeError, ValueError):
            logging.warning("Не удалось прочитать records.json — начинаем с пустой базы.")
    return {}


def save_records(records: dict[int, int]) -> None:
    """Атомично сохраняет рекорды на диск."""
    tmp = RECORD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False), "utf‑8")
    tmp.replace(RECORD_FILE)


RECORDS: dict[int, int] = load_records()

# ---------------------------------------------------------------------------
# 2. Вспомогательные функции и UI‑элементы
# ---------------------------------------------------------------------------

def generate_secret(length: int = CODE_LENGTH) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["🎮 Новая игра", "🏆 Рекорд"]], resize_keyboard=True)

# ---------------------------------------------------------------------------
# 3. Обработчики команд
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    intro = (
        "🤖 *Код‑Мастер 4*\n\n"
        "Я загадываю *секретный 4‑значный код*. Ваша цель — угадать все цифры и их позиции. "
        "После каждой попытки я показываю шаблон (пример: `4*2*`). "
        "Звёздочки `*` — ещё не раскрытые позиции.\n\n"
        "Нажмите *«🎮 Новая игра»* или отправьте код из 4 цифр."
    )
    await update.message.reply_text(intro, parse_mode="Markdown", reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n"
        "/newgame — начать новую игру\n"
        "/record — показать ваш рекорд",
        reply_markup=main_keyboard(),
    )


async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.update({
        "secret": generate_secret(),
        "revealed": ["*"] * CODE_LENGTH,
        "attempts": 0,
    })
    await update.message.reply_text("🎲 Я загадал новый 4‑значный код. Удачи!", reply_markup=main_keyboard())


async def show_record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    best = RECORDS.get(user_id)
    msg = "Вы ещё не установили рекорд. Сыграйте пару партий!" if best is None else f"Ваш лучший результат — *{best}* попыток."
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_keyboard())

# ---------------------------------------------------------------------------
# 4. Игровая логика
# ---------------------------------------------------------------------------

async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Кнопки клавиатуры
    if text == "🎮 Новая игра":
        await new_game(update, context)
        return
    if text == "🏆 Рекорд":
        await show_record(update, context)
        return

    # Валидация ввода
    if not text.isdigit() or len(text) != CODE_LENGTH:
        await update.message.reply_text(
            f"Введите ровно {CODE_LENGTH} цифры, без пробелов и букв.",
            reply_markup=main_keyboard(),
        )
        return

    # Инициализация игры при первом запросе
    if "secret" not in context.user_data:
        context.user_data.update({
            "secret": generate_secret(),
            "revealed": ["*"] * CODE_LENGTH,
            "attempts": 0,
        })

    secret: str = context.user_data["secret"]
    context.user_data["attempts"] += 1

    for i, (g_digit, s_digit) in enumerate(zip(text, secret)):
        if g_digit == s_digit:
            context.user_data["revealed"][i] = g_digit

    pattern = "".join(context.user_data["revealed"])
    await update.message.reply_text(f"Результат: `{pattern}`", parse_mode="Markdown")

    # Победа?
    if text == secret:
        attempts = context.user_data["attempts"]
        best = RECORDS.get(user_id)
        new_record = best is None or attempts < best
        if new_record:
            RECORDS[user_id] = attempts
            save_records(RECORDS)
        await update.message.reply_text(
            f"🎉 Поздравляю! Код *{secret}* угадан за *{attempts}* попыток." +
            ("\n🏆 Это новый рекорд!" if new_record else "") +
            "\nНажмите «🎮 Новая игра», чтобы сыграть снова.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        for key in ("secret", "revealed", "attempts"):
            context.user_data.pop(key, None)

# ---------------------------------------------------------------------------
# 5. Запуск с автоперезапуском при сетевых ошибках
# ---------------------------------------------------------------------------

def create_app():
    request = HTTPXRequest(connect_timeout=10.0, read_timeout=15.0, write_timeout=15.0)
    return ApplicationBuilder().token(BOT_TOKEN).request(request).build()


def main() -> None:
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

    while True:
        app = create_app()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("newgame", new_game))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("record", show_record))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_guess))

        try:
            print("Bot is running… Press Ctrl‑C to stop.")
            app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)
        except NetworkError as e:
            logging.warning("NetworkError: %s — перезапуск через 5 секунд…", e)
            time.sleep(5)
        except KeyboardInterrupt:
            print("⏹  Bot stopped by user.")
            break


if __name__ == "__main__":
    main()
