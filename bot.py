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


REPLY_CHANCE = float(os.getenv("REPLY_CHANCE", "0.1"))

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

# --- Google Gemini (контекстные ответы) ---
# Ключ: https://aistudio.google.com/apikey  (переменная GEMINI_API_KEY)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash-lite"
GEMINI_FALLBACK_MODELS = (
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
)
GEMINI_PRO_MODEL = "gemini-2.5-pro"
GEMINI_KEY_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_AI_STUDIO_KEY",
    "OPENAI_API_KEY",
    "LLM_API_KEY",
)
GEMINI_KEY_HELP = "https://aistudio.google.com/apikey"


def get_gemini_models() -> list[str]:
    if os.getenv("LLM_MODEL"):
        models = [os.getenv("LLM_MODEL", "").strip()]
    elif os.getenv("LLM_PRO", "").lower() in ("1", "true", "yes"):
        models = [GEMINI_PRO_MODEL]
    else:
        models = [GEMINI_DEFAULT_MODEL]

    for model in GEMINI_FALLBACK_MODELS:
        if model not in models:
            models.append(model)

    for model in os.getenv("LLM_FALLBACK_MODELS", "").split(","):
        model = model.strip()
        if model and model not in models:
            models.append(model)

    return models


def get_llm_config() -> tuple[str | None, str, list[str]]:
    """API-ключ, base URL и список моделей для перебора."""
    api_key = None
    key_source = None
    for name in GEMINI_KEY_VARS:
        value = os.getenv(name)
        if value:
            api_key = value.strip()
            key_source = name
            break

    custom_base = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
    base_url = custom_base or GEMINI_BASE_URL
    models = get_gemini_models()

    if api_key and key_source and os.getenv("LLM_LOG_KEY_SOURCE") == "1":
        logger.info("Gemini: ключ из переменной %s", key_source)

    return api_key, base_url, models


def log_llm_config() -> None:
    api_key, base_url, models = get_llm_config()
    if api_key:
        preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
        logger.info(
            "Gemini: %s, модели=%s, ключ=%s",
            base_url,
            " -> ".join(models),
            preview,
        )
    else:
        logger.warning(
            "GEMINI_API_KEY не задан — бот отвечает шаблонами. "
            "Ключ: %s",
            GEMINI_KEY_HELP,
        )

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))

BULLY_SYSTEM_PROMPT = """Ты токсичный телеграм-бот в групповом чате между знакомыми.
На сообщения пользователей отвечай коротко: 1–2 предложения, максимум 200 символов.
Обязательно учитывай СМЫСЛ и контекст того, что написал человек — высмеивай, подкалывай или грубо комментируй именно это, а не абстрактно.
Стиль: грубый, саркастичный, иногда мат (как в чате между друзьями). Не будь формальным и не морализируй.
Не пересказывай сообщение пользователя дословно. Не объясняй, почему ты так ответил.
Пиши только текст ответа, без кавычек, без markdown и без пояснений."""

FALLBACK_QUESTION_RESPONSES = [
    "Серьёзно ты это спрашиваешь? Сам подумай, не ленись.",
    "Вопрос уровня «я только проснулся». Гугл в помощь.",
    "Такие вопросы задают, когда мозг на перекуре.",
    "Ты это реально не знаешь или просто проверяешь, жив ли чат?",
]

FALLBACK_GREETING_RESPONSES = [
    "О, приполз. Не радуйся, это не compliment.",
    "Здарова. Только не думай, что мы скучали.",
    "Привет. Сразу предупреждаю: настроение — «иди нахуй».",
]

FALLBACK_COMPLAINT_RESPONSES = [
    "Ну и кто тебя заставлял? Сам написал — сам страдай.",
    "Жалуешься в чат, будто мы тут служба поддержки твоей жизни.",
    "Опять всем world pain? Никто не заказывал.",
]

FALLBACK_BRAG_RESPONSES = [
    "Ух ты, герой. Медаль за участие уже отправил — в мусорку.",
    "Слышал, но всё равно похуй. Расскажи ещё раз, вдруг станет интересно.",
    "Круто для тебя. Для остальных — просто шум в ленте.",
]

FALLBACK_GENERIC_RESPONSES = [
    "Написал — и сразу стало ясно, зачем кнопка mute.",
    "Твоё сообщение — как Wi‑Fi в лифте: вроде есть, но толку ноль.",
    "Прочитал. Жалею. В следующий раз просто промолчи.",
    "Если это было умно — я пропустил момент.",
    "Спасибо, теперь понятно, кого сегодня игнорить.",
]

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


async def _request_gemini(
    api_key: str,
    base_url: str,
    model: str,
    user_message: str,
) -> str | None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": BULLY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.95,
        "max_tokens": 120,
    }

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        return _clean_bully_response(content) or None


def _format_gemini_error(last_error: str) -> str:
    if "401" in last_error or "API key not valid" in last_error:
        return "неверный GEMINI_API_KEY — создай новый на aistudio.google.com/apikey"
    if "403" in last_error or "Forbidden" in last_error:
        return "Gemini отклонил запрос (403) — проверь ключ и лимиты в AI Studio"
    if "429" in last_error or "quota" in last_error.lower():
        return "лимит Gemini исчерпан — подожди или создай новый ключ/проект в AI Studio"
    if "404" in last_error or "NOT_FOUND" in last_error:
        return "модель Gemini недоступна — обнови бота или задай LLM_MODEL на Bothost"
    return last_error


async def generate_bully_response(text: str, author: str | None = None) -> str:
    user_text = text.strip()[:800]
    if not user_text:
        return fallback_bully_response(text, "пустое сообщение")

    api_key, base_url, models = get_llm_config()

    if not api_key:
        return fallback_bully_response(
            user_text,
            f"нет GEMINI_API_KEY — ключ: {GEMINI_KEY_HELP}",
        )

    user_message = user_text
    if author:
        user_message = f"[{author}]: {user_text}"

    last_error = "неизвестная ошибка"
    primary_model = models[0]

    for attempt_model in models:
        try:
            reply = await _request_gemini(
                api_key, base_url, attempt_model, user_message
            )
            if reply:
                if attempt_model != primary_model:
                    logger.info(
                        "Gemini ответил через модель %s",
                        attempt_model,
                    )
                return reply
            last_error = f"пустой ответ от модели {attempt_model}"
            logger.warning(last_error)
        except httpx.HTTPStatusError as error:
            body = error.response.text[:400] if error.response else ""
            status = error.response.status_code if error.response else "?"
            last_error = f"HTTP {status}: {body}"
            logger.warning("Gemini %s (модель %s)", last_error, attempt_model)
            if status == 401:
                break
            if attempt_model != models[-1]:
                continue
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as error:
            last_error = str(error)
            logger.warning(
                "Gemini ошибка (модель %s): %s",
                attempt_model,
                error,
            )
            if attempt_model != models[-1]:
                continue

    return fallback_bully_response(user_text, _format_gemini_error(last_error))


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

    if (
        reply_to
        and reply_to.from_user
        and reply_to.from_user.id == context.bot.id
    ):
        try:
            await message.reply_text(
                random.choice(REPLY_TO_BOT_RESPONSES)
            )
            logger.info("Ответ на reply пользователю %s", user_id)
        except Exception as error:
            logger.warning(
                "Не удалось отправить ответ на reply: %s",
                error,
                exc_info=True,
            )
        return

    if random.random() > REPLY_CHANCE:
        logger.info(
            "Пропуск по REPLY_CHANCE (%.0f%%), user_id=%s",
            REPLY_CHANCE * 100,
            user_id,
        )
        return

    author = user.first_name if user else None

    try:
        logger.info("Генерирую ответ для user_id=%s...", user_id)
        reply = await generate_bully_response(text, author=author)
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
