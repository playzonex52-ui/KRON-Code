"""
Telegram Moderatsiya Boti (Gemini AI bilan)
"""

import logging
from google import genai
from collections import defaultdict
from datetime import datetime, timedelta

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import os

# ===================== SOZLAMALAR =====================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

MUTE_DURATION_MINUTES = 60
DELETE_WARNING_AFTER = 30

# ======================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)
warnings = defaultdict(lambda: defaultdict(int))


async def is_offensive(text: str) -> bool:
    try:
        prompt = f"""Quyidagi xabar haqoratli, so'kinish yoki tahqirlash so'zlarini o'z ichiga oladimi?
Xabar: "{text}"

Faqat "ha" yoki "yoq" deb javob ber. Boshqa hech narsa yozma.
- "ha" = haqorat bor
- "yoq" = haqorat yo'q"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        answer = response.text.strip().lower()
        return answer.startswith("ha")
    except Exception as e:
        logger.error(f"Gemini xatosi: {e}")
        return False


def get_user_mention(user) -> str:
    if user.username:
        return f"@{user.username}"
    return f'<a href="tg://user?id={user.id}">{user.full_name}</a>'


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user = message.from_user
    chat = message.chat

    if chat.type not in ["group", "supergroup"]:
        return

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ["creator", "administrator"]:
            return
    except Exception:
        pass

    offensive = await is_offensive(message.text)
    if not offensive:
        return

    try:
        await message.delete()
    except Exception:
        logger.warning("Xabarni o'chirib bo'lmadi")

    chat_id = chat.id
    user_id = user.id
    warnings[chat_id][user_id] += 1
    count = warnings[chat_id][user_id]
    mention = get_user_mention(user)

    if count == 1:
        text = (
            f"⚠️ {mention}, iltimos <b>sokinmang</b>!\n\n"
            f"Bu sizning <b>1-ogohlantirishingiz</b>. "
            f"Keyingi safar qattiqroq chora ko'riladi."
        )
    elif count == 2:
        text = (
            f"🚨 {mention}, bu sizning <b>2-ogohlantirishingiz</b>!\n\n"
            f"⛔ Yana bir bor haqorat qilsangiz, "
            f"<b>{MUTE_DURATION_MINUTES} daqiqaga ovozingiz o'chiriladi</b>.\n"
            f"Iltimos, qoidalarga rioya qiling!"
        )
    elif count >= 3:
        mute_until = datetime.now() + timedelta(minutes=MUTE_DURATION_MINUTES)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
                until_date=mute_until,
            )
            text = (
                f"🔇 {mention} <b>{MUTE_DURATION_MINUTES} daqiqaga</b> mute qilindi!\n\n"
                f"Bu sizning <b>3-ogohlantirishingiz</b> edi. "
                f"Guruh qoidalarini buzishni to'xtating."
            )
            warnings[chat_id][user_id] = 0
        except Exception as e:
            logger.error(f"Mute xatosi: {e}")
            text = (
                f"⛔ {mention}, siz qoidalarni qayta-qayta buzmoqdasiz!\n"
                f"(Botga to'liq admin huquqi bering!)"
            )
    else:
        return

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )

    if DELETE_WARNING_AFTER and sent:
        context.job_queue.run_once(
            delete_message_job,
            when=DELETE_WARNING_AFTER,
            data={"chat_id": chat_id, "message_id": sent.message_id},
        )


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"]
        )
    except Exception:
        pass


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = message.chat
    user = message.from_user

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ["creator", "administrator"]:
            await message.reply_text("❌ Bu buyruq faqat adminlar uchun!")
            return
    except Exception:
        return

    if not context.args:
        await message.reply_text("Ishlatish: /reset @username")
        return

    target = context.args[0].lstrip("@")
    try:
        target_id = int(target)
    except ValueError:
        try:
            chat_member = await context.bot.get_chat_member(chat.id, target)
            target_id = chat_member.user.id
        except Exception:
            await message.reply_text("❌ Foydalanuvchi topilmadi.")
            return

    warnings[chat.id][target_id] = 0
    await message.reply_text("✅ Ogohlantirishlar tozalandi.")


async def warnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = message.chat
    user = message.from_user

    count = warnings[chat.id][user.id]
    await message.reply_text(
        f"📊 Sizda hozirda <b>{count} ta</b> ogohlantirish bor.\n"
        f"3 ta bo'lganda mute qilinasiz.",
        parse_mode="HTML"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Men AI moderatsiya botiman.\n\n"
        "🤖 Gemini AI yordamida har qanday haqoratni aniqlayman!\n\n"
        "📋 Buyruqlar:\n"
        "/warnings — ogohlantirish sonini ko'rish\n"
        "/reset @user — (admin) ogohlantirishni tozalash\n\n"
        "Meni guruhga admin qiling va ishlata boshlang!"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("warnings", warnings_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot ishga tushdi (Gemini AI bilan)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
