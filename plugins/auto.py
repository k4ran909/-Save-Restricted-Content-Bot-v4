# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

import os
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message
from config import LOG_GROUP, API_ID, API_HASH
from shared_client import app as bot
from plugins.batch import get_uclient
from utils.func import get_user_data_key, process_text_with_rules

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dictionary to track active auto listeners: { user_id: { "target_id": int, "target_name": str, "handler": MessageHandler, "client": Client } }
ACTIVE_LISTENERS = {}

async def forward_content_to_log(uc: Client, message: Message, user_id: int, target_name: str):
    """Handles forwarding / saving of incoming messages to LOG_GROUP or configured chat."""
    try:
        dest_chat = await get_user_data_key(user_id, 'chat_id', None)
        dest_chat_id = int(dest_chat.split('/')[0]) if dest_chat else LOG_GROUP

        # Try direct copy first
        try:
            await message.copy(dest_chat_id)
            logger.info(f"Auto-saved message directly from {target_name} to {dest_chat_id}")
            return
        except Exception as copy_err:
            logger.info(f"Direct copy failed ({copy_err}), attempting download & upload...")

        # If restricted or copy fails, download and upload using bot or userbot
        if message.media:
            downloaded = await uc.download_media(message)
            if not downloaded:
                return

            caption = message.caption or ""
            proc_caption = await process_text_with_rules(str(user_id), caption) if caption else ""

            try:
                if message.video:
                    await bot.send_video(dest_chat_id, downloaded, caption=proc_caption or None)
                elif message.document:
                    await bot.send_document(dest_chat_id, downloaded, caption=proc_caption or None)
                elif message.audio:
                    await bot.send_audio(dest_chat_id, downloaded, caption=proc_caption or None)
                elif message.photo:
                    await bot.send_photo(dest_chat_id, downloaded, caption=proc_caption or None)
                elif message.voice:
                    await bot.send_voice(dest_chat_id, downloaded)
                elif message.video_note:
                    await bot.send_video_note(dest_chat_id, downloaded)
                elif message.sticker:
                    await bot.send_sticker(dest_chat_id, message.sticker.file_id)
                logger.info(f"Auto-uploaded media from {target_name} to {dest_chat_id}")
            finally:
                if downloaded and os.path.exists(downloaded):
                    try:
                        os.remove(downloaded)
                    except Exception:
                        pass
        elif message.text:
            await bot.send_message(dest_chat_id, message.text)
    except Exception as e:
        logger.error(f"Error forwarding auto message from {target_name}: {e}")


@bot.on_message(filters.command("auto") & filters.private)
async def auto_command_handler(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.strip().split()

    if len(args) < 2:
        help_text = (
            "🤖 **Auto Save / Forwarder from Bot**\n\n"
            "**Usage:**\n"
            "• `/auto all <bot_username or bot_id>` — Automatically listen and forward all videos/media from that bot to your log channel.\n"
            "• `/auto stop` — Stop auto-forwarding.\n"
            "• `/auto status` — Check current active auto-forwarding status.\n\n"
            "⚠️ *Make sure you have logged in using `/login` first.*"
        )
        await message.reply_text(help_text)
        return

    sub_command = args[1].lower()

    if sub_command == "stop":
        if user_id in ACTIVE_LISTENERS:
            info = ACTIVE_LISTENERS[user_id]
            uc = info.get("client")
            handler = info.get("handler")
            target_name = info.get("target_name", "Target")
            if uc and handler:
                try:
                    uc.remove_handler(*handler)
                except Exception:
                    pass
            del ACTIVE_LISTENERS[user_id]
            await message.reply_text(f"🛑 **Auto-forwarding stopped** for `{target_name}`.")
        else:
            await message.reply_text("ℹ️ You don't have any active auto-forwarding task running.")
        return

    elif sub_command == "status":
        if user_id in ACTIVE_LISTENERS:
            info = ACTIVE_LISTENERS[user_id]
            target_name = info.get("target_name", "Unknown")
            target_id = info.get("target_id", "Unknown")
            await message.reply_text(
                f"🟢 **Auto-Forwarding is Active!**\n\n"
                f"• **Target Bot:** `{target_name}` (ID: `{target_id}`)\n"
                f"• **Destination:** Log Channel (`{LOG_GROUP}`)\n\n"
                f"To stop listening, send `/auto stop`."
            )
        else:
            await message.reply_text("ℹ️ No active auto-forwarding listener found. Start one with `/auto all <bot_username>`.")
        return

    elif sub_command == "all":
        if len(args) < 3:
            await message.reply_text(
                "⚠️ **Please provide the bot username or ID.**\n"
                "Example: `/auto all @ExampleBot` or `/auto all 123456789`"
            )
            return

        target_input = args[2].strip().replace("https://t.me/", "").replace("@", "")

        status_msg = await message.reply_text("🔍 Connecting to your account and setting up listener...")

        uc = await get_uclient(user_id)
        if not uc:
            await status_msg.edit_text("❌ **Login Required:** Please login to your account using `/login` first.")
            return

        try:
            # Resolve target bot
            if target_input.lstrip("-").isdigit():
                target_chat_id = int(target_input)
                chat = await uc.get_chat(target_chat_id)
            else:
                chat = await uc.get_chat(target_input)
                target_chat_id = chat.id

            target_name = chat.username or chat.title or chat.first_name or str(target_chat_id)
        except Exception as resolve_err:
            await status_msg.edit_text(f"❌ **Failed to find bot/chat:** `{target_input}`\nError: {resolve_err}")
            return

        # If an existing listener is running for this user, stop it first
        if user_id in ACTIVE_LISTENERS:
            old_info = ACTIVE_LISTENERS[user_id]
            old_uc = old_info.get("client")
            old_handler = old_info.get("handler")
            if old_uc and old_handler:
                try:
                    old_uc.remove_handler(*old_handler)
                except Exception:
                    pass
            del ACTIVE_LISTENERS[user_id]

        # Define handler callback for this specific target chat
        async def on_new_bot_message(client: Client, bot_message: Message):
            if bot_message.chat and bot_message.chat.id == target_chat_id:
                await forward_content_to_log(client, bot_message, user_id, target_name)

        # Register message handler on user client
        handler = MessageHandler(on_new_bot_message, filters.chat(target_chat_id))
        uc.add_handler(handler)

        ACTIVE_LISTENERS[user_id] = {
            "target_id": target_chat_id,
            "target_name": target_name,
            "handler": (handler, 0),
            "client": uc
        }

        await status_msg.edit_text(
            f"✅ **Auto-Forwarding Activated!** 🤖\n\n"
            f"• **Target:** `@{target_name}` (ID: `{target_chat_id}`)\n"
            f"• **Destination:** Log Channel (`{LOG_GROUP}`)\n\n"
            f"All incoming videos, media, and responses from this bot will now be automatically forwarded to your log channel in real-time.\n\n"
            f"To stop listening anytime, send `/auto stop`."
        )
    else:
        await message.reply_text("⚠️ Invalid format. Usage: `/auto all <bot_username>` or `/auto stop`")
