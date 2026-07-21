import os

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)


async def delete_moai(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if message is None:
        return

    text = message.text or message.caption or ""

    if "🗿" in text:
        try:
            await message.delete()
        except Exception as error:
            print(f"Не удалось удалить сообщение: {error}")


def main():
    token = "8369198534:AAFUm8r4BkuR-qrPYdbQKBEAgrN5MfSl3f8"

    app = Application.builder().token(token).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.CaptionRegex("🗿"),
            delete_moai
        )
    )

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()