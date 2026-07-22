import io
import json
import logging
import os
import random
import sys
import traceback
import urllib.request
from pathlib import Path

import httpx
from PIL import Image
import imagehash

# Сразу пишем в stderr — Bothost/Docker могут буферизовать stdout
sys.stderr.write("[bot] bot.py загружается...\n")
sys.stderr.flush()

from telegram import MessageEntity, Update
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


REPLY_CHANCE = float(os.getenv("REPLY_CHANCE", "0.05"))

MOAI_EMOJI = "🗿"
MOAI_KEYWORDS = ("moai", "moyai", "моаи", "moais", "rapanui", "easter")
MOAI_REF_DIR = Path(__file__).resolve().parent / "assets" / "moai_refs"
MOAI_REF_URLS = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Moai_Rano_raraku.jpg/220px-Moai_Rano_raraku.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/27/Moai_Ika_Tere_Ahu_Tongariki.jpg/220px-Moai_Ika_Tere_Ahu_Tongariki.jpg",
)
MOAI_HASH_CHECKS = (
    (imagehash.phash, 20),
    (imagehash.dhash, 14),
    (imagehash.whash, 22),
)
MOAI_REF_HASHES: dict[str, list[imagehash.ImageHash]] = {
    "phash": [],
    "dhash": [],
    "whash": [],
}

# --- DeepSeek (контекстные ответы) ---
# Ключ: https://platform.deepseek.com/api_keys  (переменная DEEPSEEK_API_KEY)
# Баланс: https://platform.deepseek.com
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_FALLBACK_MODELS = ("deepseek-chat", "deepseek-v4-flash")
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"
DEEPSEEK_KEY_VARS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")
DEEPSEEK_KEY_HELP = "https://platform.deepseek.com/api_keys"


def get_deepseek_models() -> list[str]:
    if os.getenv("LLM_MODEL"):
        models = [os.getenv("LLM_MODEL", "").strip()]
    elif os.getenv("LLM_PRO", "").lower() in ("1", "true", "yes"):
        models = [DEEPSEEK_PRO_MODEL]
    else:
        models = [DEEPSEEK_DEFAULT_MODEL]

    for model in DEEPSEEK_FALLBACK_MODELS:
        if model not in models:
            models.append(model)

    for model in os.getenv("LLM_FALLBACK_MODELS", "").split(","):
        model = model.strip()
        if model and model not in models:
            models.append(model)

    return models


def get_deepseek_config() -> tuple[str | None, str, list[str], str | None]:
    api_key = None
    key_source = None
    for name in DEEPSEEK_KEY_VARS:
        value = os.getenv(name)
        if value:
            api_key = value.strip()
            key_source = name
            break

    custom_base = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
    base_url = custom_base or DEEPSEEK_BASE_URL
    return api_key, base_url, get_deepseek_models(), key_source


def log_llm_config() -> None:
    api_key, base_url, models, key_source = get_deepseek_config()
    if api_key:
        preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
        logger.info(
            "DeepSeek: %s, модели=%s, ключ=%s (%s)",
            base_url,
            " -> ".join(models),
            preview,
            key_source,
        )
    else:
        logger.warning(
            "DEEPSEEK_API_KEY не задан — бот отвечает шаблонами. "
            "Ключ: %s",
            DEEPSEEK_KEY_HELP,
        )

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))

BULLY_SYSTEM_PROMPT = """Ты максимально токсичный и агрессивный телеграм-бот в групповом чате между знакомыми.
На сообщения отвечай коротко: 1–2 предложения, максимум 200 символов.
Обязательно учитывай СМЫСЛ того, что написал человек — жёстко высмеивай, подкалывай, уничижай именно это.
Стиль: агрессивный, злой, саркастичный. Мат обязателен — используй его свободно и естественно (хуй, бля, пиздец, нахуй, заебал, иди нахуй и т.п.), но в каждом ответе хотя бы одно матерное слово или грубая фраза.
Будь прямолинейным и злым, как самый токсичный участник чата. Не сдерживайся, не извиняйся, не морализируй.
Не пересказывай сообщение дословно. Не объясняй свой ответ.
Пиши только текст ответа, без кавычек, без markdown и без пояснений."""

FALLBACK_QUESTION_RESPONSES = [
    "Ты реально это спрашиваешь, бля? Сам думай, не тупи.",
    "Вопрос на уровне «я только проснулся, нахуй». Гугл в помощь.",
    "Какого хуя ты это спрашиваешь? Мозг включи.",
    "Ты серьёзно не знаешь или просто заебал чат?",
]

FALLBACK_GREETING_RESPONSES = [
    "О, приполз, сука. Не радуйся — тебя никто не ждал.",
    "Здарова. Только сразу: иди нахуй.",
    "Привет. Настроение — послать тебя нахуй, так что не начинай.",
    "О, ты. Бля, опять ты.",
]

FALLBACK_COMPLAINT_RESPONSES = [
    "Ну и кто тебя заставлял, бля? Сам написал — сам и страдай.",
    "Заебал ныть в чат. Мы тут не твоя психологическая поддержка.",
    "Опять всем своё говно вылил? Никто не заказывал, нахуй.",
    "Пожаловался — молодец. Теперь иди нахуй с этим.",
]

FALLBACK_BRAG_RESPONSES = [
    "Ух ты, герой нахуй. Медаль за участие — в жопу.",
    "Слышал, похуй. Расскажи ещё раз, вдруг кому-то станет не похуй — никому.",
    "Круто для тебя, бля. Для остальных — просто шум заебал.",
    "Вау, ахуенно. Теперь иди нахуй с этой хвастовой.",
]

FALLBACK_GENERIC_RESPONSES = [
    "Написал — и сразу понятно, зачем кнопка mute, бля.",
    "Прочитал. Жалею, что прочитал. В следующий раз просто заткнись.",
    "Твоё сообщение — полная хуйня, если честно.",
    "Спасибо, теперь знаю, кого сегодня посылать нахуй.",
    "Заебал уже. Закрой рот и не открывай.",
]


def _clean_bully_response(raw: str) -> str:
    text = raw.strip().strip("\"'«»")
    if text.lower().startswith("ответ:"):
        text = text.split(":", 1)[1].strip()
    if len(text) > 300:
        text = text[:297].rstrip() + "..."
    return text


def fallback_bully_response(text: str, reason: str = "неизвестно") -> str:
    logger.warning("Ответ из шаблонов (%s)", reason)
    lower = text.lower()

    if "?" in text or lower.startswith(("как ", "что ", "где ", "когда ", "зачем ", "почему ")):
        return random.choice(FALLBACK_QUESTION_RESPONSES)

    if any(word in lower for word in ("привет", "здарова", "здорово", "хай", "hello", "hi")):
        return random.choice(FALLBACK_GREETING_RESPONSES)

    if any(word in lower for word in ("устал", "бесит", "заеб", "плохо", "жал", "надоел", "достал")):
        return random.choice(FALLBACK_COMPLAINT_RESPONSES)

    if any(word in lower for word in ("я ", "мне ", "сделал", "купил", "выиграл", "получил", "красав")):
        return random.choice(FALLBACK_BRAG_RESPONSES)

    return random.choice(FALLBACK_GENERIC_RESPONSES)


async def _request_deepseek(
    api_key: str,
    base_url: str,
    model: str,
    user_message: str,
) -> str | None:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": BULLY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 1.1,
        "max_tokens": 120,
    }
    if model.startswith("deepseek-v4"):
        payload["thinking"] = {"type": "disabled"}

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        return _clean_bully_response(content) or None


def _format_deepseek_error(last_error: str) -> str:
    lower = last_error.lower()
    if "402" in last_error or "insufficient balance" in lower:
        return "нет денег на балансе DeepSeek — пополни на platform.deepseek.com"
    if "401" in last_error or "api key" in lower:
        return "неверный DEEPSEEK_API_KEY — ключ: platform.deepseek.com/api_keys"
    if "429" in last_error or "quota" in lower:
        return "лимит DeepSeek — подожди минуту"
    if "404" in last_error or "not_found" in lower:
        return "модель DeepSeek недоступна — задай LLM_MODEL=deepseek-chat"
    return last_error


async def generate_bully_response(
    text: str,
    author: str | None = None,
    *,
    replied_to_bot_text: str | None = None,
) -> str:
    user_text = text.strip()[:800]
    if not user_text:
        return fallback_bully_response(text, "пустое сообщение")

    api_key, base_url, models, _key_source = get_deepseek_config()
    if not api_key:
        return fallback_bully_response(
            user_text,
            f"нет DEEPSEEK_API_KEY — ключ: {DEEPSEEK_KEY_HELP}",
        )

    if replied_to_bot_text:
        bot_text = replied_to_bot_text.strip()[:300]
        user_message = (
            f"Пользователь ответил (reply) на твоё сообщение «{bot_text}». "
            f"Его текст: {user_text}. "
            "Ответь максимально агрессивно — он полез тебе отвечать."
        )
    else:
        user_message = user_text

    if author:
        user_message = f"[{author}]: {user_message}"

    last_error = "неизвестная ошибка"
    primary_model = models[0]

    for attempt_model in models:
        try:
            reply = await _request_deepseek(
                api_key, base_url, attempt_model, user_message
            )
            if reply:
                if attempt_model != primary_model:
                    logger.info(
                        "DeepSeek ответил через модель %s",
                        attempt_model,
                    )
                return reply
            last_error = f"пустой ответ от модели {attempt_model}"
            logger.warning(last_error)
        except httpx.HTTPStatusError as error:
            body = error.response.text[:400] if error.response else ""
            status = error.response.status_code if error.response else "?"
            last_error = f"HTTP {status}: {body}"
            logger.warning("DeepSeek %s (модель %s)", last_error, attempt_model)
            if status in (401, 402):
                break
            if attempt_model != models[-1]:
                continue
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as error:
            last_error = str(error)
            logger.warning(
                "DeepSeek ошибка (модель %s): %s",
                attempt_model,
                error,
            )
            if attempt_model != models[-1]:
                continue

    return fallback_bully_response(user_text, _format_deepseek_error(last_error))


def _prepare_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    if getattr(image, "is_animated", False):
        image.seek(0)
    return image.convert("RGB")


def _add_reference_image(image: Image.Image, source: str):
    for hash_func, _threshold in MOAI_HASH_CHECKS:
        hash_name = hash_func.__name__
        MOAI_REF_HASHES[hash_name].append(hash_func(image))
    logger.info("Добавлен эталон Moai: %s", source)


def load_moai_reference_hashes():
    for hash_name in MOAI_REF_HASHES:
        MOAI_REF_HASHES[hash_name].clear()

    file_count = 0
    if MOAI_REF_DIR.is_dir():
        for path in sorted(MOAI_REF_DIR.iterdir()):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            try:
                image = Image.open(path).convert("RGB")
                _add_reference_image(image, path.name)
                file_count += 1
            except Exception as error:
                logger.warning(
                    "Не удалось загрузить локальный эталон %s: %s",
                    path.name,
                    error,
                )

    for url in MOAI_REF_URLS:
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                data = response.read()
            image = _prepare_image(data)
            _add_reference_image(image, url)
            file_count += 1
        except Exception as error:
            logger.warning("Не удалось загрузить эталон Moai %s: %s", url, error)

    if file_count:
        logger.info("Эталонов Moai для сравнения: %d", file_count)
    else:
        logger.warning(
            "Эталоны Moai не загрузились — фото будут проверяться "
            "только по эмодзи, подписи, стикерам и эвристике"
        )


def looks_like_moai_illustration(image: Image.Image) -> bool:
    """Эвристика для мультяшных/нарисованных Moai на светлом фоне."""
    small = image.resize((64, 64))
    pixels = list(small.getdata())

    light_pixels = sum(
        1 for red, green, blue in pixels
        if red > 220 and green > 220 and blue > 220
    )
    if light_pixels / len(pixels) < 0.25:
        return False

    def is_stone_color(red: int, green: int, blue: int) -> bool:
        brightness = (red + green + blue) / 3
        return brightness < 145 and blue >= red - 25 and blue >= green - 35

    center_stone = 0
    upper_stone = 0
    for y in range(64):
        for x in range(64):
            red, green, blue = pixels[y * 64 + x]
            if not is_stone_color(red, green, blue):
                continue
            if 18 <= x <= 45:
                center_stone += 1
            if 12 <= x <= 50 and 8 <= y <= 42:
                upper_stone += 1

    return center_stone >= 90 and upper_stone >= 60


def has_moai_text(text: str | None) -> bool:
    if not text:
        return False
    if MOAI_EMOJI in text:
        return True
    lower = text.lower()
    return any(keyword in lower for keyword in MOAI_KEYWORDS)


def has_moai_entities(message) -> bool:
    entities = list(message.entities or ()) + list(message.caption_entities or ())
    for entity in entities:
        if entity.type == MessageEntity.CUSTOM_EMOJI:
            chunk = (message.text or message.caption or "")[
                entity.offset : entity.offset + entity.length
            ]
            if MOAI_EMOJI in chunk:
                return True
    return False


def is_moai_sticker(sticker) -> bool:
    if sticker.emoji == MOAI_EMOJI:
        return True
    set_name = (sticker.set_name or "").lower()
    return any(keyword in set_name for keyword in MOAI_KEYWORDS)


def image_matches_moai(image_bytes: bytes) -> bool:
    try:
        image = _prepare_image(image_bytes)
    except Exception as error:
        logger.warning("Не удалось проанализировать изображение: %s", error)
        return False

    for hash_func, threshold in MOAI_HASH_CHECKS:
        hash_name = hash_func.__name__
        references = MOAI_REF_HASHES.get(hash_name, [])
        if not references:
            continue

        image_hash = hash_func(image)
        if any(image_hash - reference_hash <= threshold for reference_hash in references):
            logger.info("Moai найден по hash %s", hash_name)
            return True

    if looks_like_moai_illustration(image):
        logger.info("Moai найден по эвристике иллюстрации")
        return True

    return False


async def download_message_image(message, context) -> bytes | None:
    file_id = None

    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.sticker and not message.sticker.is_video:
        file_id = message.sticker.file_id
    elif message.animation:
        file_id = message.animation.file_id
    elif (
        message.document
        and message.document.mime_type
        and message.document.mime_type.startswith("image/")
    ):
        file_id = message.document.file_id

    if not file_id:
        return None

    try:
        telegram_file = await context.bot.get_file(file_id)
        buffer = io.BytesIO()
        await telegram_file.download_to_memory(buffer)
        return buffer.getvalue()
    except Exception as error:
        logger.warning("Не удалось скачать файл для проверки Moai: %s", error)
        return None


async def is_moai_message(message, context) -> bool:
    if has_moai_text(message.text) or has_moai_text(message.caption):
        return True

    if has_moai_entities(message):
        return True

    if message.sticker and is_moai_sticker(message.sticker):
        return True

    image_bytes = await download_message_image(message, context)
    if image_bytes and image_matches_moai(image_bytes):
        return True

    return False


async def try_delete_moai(message, context) -> bool:
    if message.from_user and message.from_user.is_bot:
        return False

    try:
        is_moai = await is_moai_message(message, context)
    except Exception as error:
        logger.warning(
            "Ошибка проверки Moai, пропускаю: %s",
            error,
            exc_info=True,
        )
        return False

    if not is_moai:
        return False

    try:
        await message.delete()
        logger.info(
            "Удалено сообщение с Moai от user_id=%s",
            message.from_user.id if message.from_user else "?",
        )
        return True
    except Exception as error:
        logger.warning("Не удалось удалить сообщение с Moai: %s", error)
        return False


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    chat = message.chat
    user = message.from_user
    user_id = user.id if user else "?"
    chat_label = f"{chat.type}:{chat.id}"

    if await try_delete_moai(message, context):
        logger.info("Moai удалён, user_id=%s, chat=%s", user_id, chat_label)
        return

    text = message.text or message.caption or ""

    if not text:
        logger.debug("Пропуск без текста, user_id=%s, chat=%s", user_id, chat_label)
        return

    if user and user.is_bot:
        return

    logger.info(
        "Входящее: user_id=%s, chat=%s, текст=%r",
        user_id,
        chat_label,
        text[:120],
    )

    reply_to = message.reply_to_message
    author = user.first_name if user else None
    is_reply_to_bot = (
        reply_to
        and reply_to.from_user
        and reply_to.from_user.id == context.bot.id
    )

    if not is_reply_to_bot and random.random() > REPLY_CHANCE:
        logger.info(
            "Пропуск по REPLY_CHANCE (%.0f%%), user_id=%s",
            REPLY_CHANCE * 100,
            user_id,
        )
        return

    bot_message_text = None
    if is_reply_to_bot:
        bot_message_text = reply_to.text or reply_to.caption or ""

    try:
        logger.info(
            "Генерирую ответ для user_id=%s%s...",
            user_id,
            " (reply на бота)" if is_reply_to_bot else "",
        )
        reply = await generate_bully_response(
            text,
            author=author,
            replied_to_bot_text=bot_message_text if is_reply_to_bot else None,
        )
        await message.reply_text(reply)
        logger.info("Ответ отправлен user_id=%s: %r", user_id, reply[:120])
    except Exception as error:
        logger.warning(
            "Не удалось отправить ответ user_id=%s: %s",
            user_id,
            error,
            exc_info=True,
        )


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
            (
                filters.TEXT
                | filters.CAPTION
                | filters.PHOTO
                | filters.Sticker.ALL
                | filters.ANIMATION
                | filters.Document.IMAGE
            ),
            handle_message,
        )
    )

    load_moai_reference_hashes()
    log_llm_config()

    logger.info("REPLY_CHANCE=%.0f%%", REPLY_CHANCE * 100)
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
