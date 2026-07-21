import logging
import os
import random
import re
import sys
import traceback

# Сразу пишем в stderr — Bothost/Docker могут буферизовать stdout
sys.stderr.write("[bot] bot.py загружается...\n")
sys.stderr.flush()

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr),
    ],
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# =========================
# НАСТРОЙКИ
# =========================

def get_token():
    for name in ("BOT_TOKEN", "API_TOKEN", "TELEGRAM_BOT_TOKEN"):
        token = os.getenv(name)
        if token:
            logger.info("Токен получен из переменной %s", name)
            return token.strip()
    return None


REPLY_CHANCE = 0.1

REPLY_TO_BOT_RESPONSES = [
    "Иди нахуй",
    "Нахуй иди",
    "Пошёл нахуй",
    "Иди нахуй, не мешай",
    "Нахуй с пляжа",
    "Съебись нахуй",
    "Иди нахуй и не возвращайся",
    "Нахуй иди, я занят",
    "Тебе сюда нахуй не надо",
    "Иди нахуй, я тут главный",
    "Нахуй иди, разговаривать не с кем",
    "Слышь, иди нахуй",
    "Нахуй иди, не отвлекай",
    "Нахуй иди, я тебя не спрашивал",
    "Иди нахуй, ответил — уже лишнее",
    "Нахуй иди, сам разберусь",
    "Иди нахуй, не reply'й мне",
    "Иди нахуй, у меня дела",
    "Нахуй иди, не надо мне тут",
    "Иди нахуй, я не обязан отвечать",
    "Нахуй иди, ты мне не начальник",
    "Иди нахуй, раз уж полез",
    "Нахуй иди, я тебе ничего не должен",
    "Иди нахуй, не трогай мои сообщения",
    "Нахуй иди, я тут не для тебя",
    "Нахуй иди, я тебя не звал",
]


WORD_PATTERN = re.compile(r"^([\w\-']+)([,.!?…:;]*)$", re.UNICODE)

SUFFIXES = ["мс", "м", "с", "ъ", "ок", "ик", "ух", "я"]
INSERT_CHARS = ["м", "с", "ъ", "ы", "о"]
VOWEL_SWAPS = {
    "а": "о",
    "о": "а",
    "е": "и",
    "и": "е",
    "у": "ю",
    "ю": "у",
    "я": "а",
    "ы": "и",
}


def _distort_word_core(core: str) -> str:
    if len(core) < 2:
        return core + random.choice(SUFFIXES)

    method = random.choice(
        ("suffix", "double", "insert", "swap", "vowel", "repeat_end")
    )

    if method == "suffix":
        return core + random.choice(SUFFIXES)

    if method == "double":
        index = random.randrange(len(core))
        letter = core[index]
        return core[: index + 1] + letter + core[index + 1 :]

    if method == "insert":
        index = random.randrange(1, len(core))
        return core[:index] + random.choice(INSERT_CHARS) + core[index:]

    if method == "swap" and len(core) >= 3:
        index = random.randrange(len(core) - 1)
        chars = list(core)
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
        return "".join(chars)

    if method == "vowel":
        chars = list(core)
        vowel_indexes = [
            index
            for index, char in enumerate(chars)
            if char.lower() in VOWEL_SWAPS
        ]
        if vowel_indexes:
            index = random.choice(vowel_indexes)
            char = chars[index]
            replacement = VOWEL_SWAPS[char.lower()]
            chars[index] = replacement.upper() if char.isupper() else replacement
            return "".join(chars)

    # repeat_end — «проспал» -> «проспаал»
    return core + core[-1] + random.choice(SUFFIXES[:3])


def distort_word(word: str) -> str:
    match = WORD_PATTERN.match(word)
    if not match:
        return word

    core, punctuation = match.group(1), match.group(2)
    if not core or core.isdigit():
        return word

    return _distort_word_core(core) + punctuation


def distort_text(text: str) -> str:
    words = text.split()
    if not words:
        return text

    # Коверкаем 60–100% слов, минимум одно
    min_distorted = max(1, len(words) // 2)
    max_distorted = len(words)
    target_count = random.randint(min_distorted, max_distorted)

    indexes = list(range(len(words)))
    random.shuffle(indexes)
    distort_indexes = set(indexes[:target_count])

    new_words = []
    for index, word in enumerate(words):
        if index in distort_indexes:
            new_words.append(distort_word(word))
        else:
            new_words.append(word)

    return " ".join(new_words)

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    text = message.text or message.caption or ""

    if not text:
        return

    if "🗿" in text:
        try:
            await message.delete()
        except Exception as error:
            logger.warning("Не удалось удалить сообщение: %s", error)
        return

    if message.from_user and message.from_user.is_bot:
        return

    reply_to = message.reply_to_message

    if (
        reply_to
        and reply_to.from_user
        and reply_to.from_user.id == context.bot.id
    ):
        try:
            await message.reply_text(
                random.choice(REPLY_TO_BOT_RESPONSES)
            )
        except Exception as error:
            logger.warning("Не удалось отправить ответ на reply: %s", error)
        return

    if random.random() > REPLY_CHANCE:
        return

    distorted = distort_text(text)

    try:
        await message.reply_text(distorted)
    except Exception as error:
        logger.warning("Не удалось отправить ответ: %s", error)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Необработанная ошибка: %s",
        context.error,
        exc_info=context.error,
    )


def main():
    logger.info("Запуск bot.py")

    token = get_token()

    if not token:
        logger.error(
            "BOT_TOKEN не найден. "
            "Проверь токен в настройках бота на Bothost."
        )
        sys.exit(1)

    app = (
        Application
        .builder()
        .token(token)
        .build()
    )

    app.add_error_handler(on_error)

    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.CAPTION,
            handle_message,
        )
    )

    logger.info("Бот запущен, начинаю polling...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error("Критическая ошибка при запуске:\n%s", traceback.format_exc())
        sys.exit(1)
