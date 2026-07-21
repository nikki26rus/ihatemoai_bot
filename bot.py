import random
import re

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================
# НАСТРОЙКИ
# =========================

TOKEN = "8369198534:AAFUm8r4BkuR-qrPYdbQKBEAgrN5MfSl3f8"

# Вероятность ответа на сообщение.
# 0.05 = 5%
REPLY_CHANCE = 0.1


# Ответы, когда пишут в reply на сообщение бота
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


# =========================
# ИСКАЖЕНИЕ ТЕКСТА
# =========================

def distort_text(text):
    """
    Немного искажает текст.
    Например:
    "Блин, я проспал"
    ->
    "Блинмс, я проспалмс"
    """

    words = text.split()

    if not words:
        return text

    new_words = []

    for word in words:

        # Иногда добавляем "мс" в конец слова
        if random.random() < 0.35:

            # Сохраняем знаки препинания в конце
            match = re.match(r"^(.*?)([,.!?]*)$", word)

            if match:
                word_without_punctuation = match.group(1)
                punctuation = match.group(2)

                word = (
                    word_without_punctuation
                    + random.choice(["мс", "м", "с"])
                    + punctuation
                )

        new_words.append(word)

    result = " ".join(new_words)

    return result


# =========================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if message is None:
        return

    text = message.text or message.caption or ""

    if not text:
        return


    # =========================
    # УДАЛЕНИЕ 🗿
    # =========================

    if "🗿" in text:

        try:
            await message.delete()
            return

        except Exception as error:
            print(
                f"Не удалось удалить сообщение: {error}"
            )


    # =========================
    # СЛУЧАЙНЫЙ ОТВЕТ
    # =========================

    # Не отвечаем на сообщения самого бота
    if message.from_user and message.from_user.is_bot:
        return


    # =========================
    # ОТВЕТ НА REPLY К СООБЩЕНИЮ БОТА
    # =========================

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
            print(
                f"Не удалось отправить ответ на reply: {error}"
            )
        return


    # С вероятностью REPLY_CHANCE отвечаем
    if random.random() > REPLY_CHANCE:
        return


    # Искажаем текст
    distorted = distort_text(text)


    # Отвечаем на исходное сообщение
    try:

        await message.reply_text(
            distorted
        )

    except Exception as error:

        print(
            f"Не удалось отправить ответ: {error}"
        )


# =========================
# ЗАПУСК БОТА
# =========================

def main():

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )


    # Обрабатываем текстовые сообщения
    app.add_handler(
        MessageHandler(
            filters.TEXT,
            handle_message
        )
    )


    print("Бот запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
