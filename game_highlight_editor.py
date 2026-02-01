import logging
import asyncio
import secrets
import hashlib
import time
import json
import pickle
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Message
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# КОНФИГУРАЦИЯ
BOT_TOKEN = "8454988958:AAHQmlOMtfTNOLsbPVNSLqyTAAYjJwEiltg"
ADMIN_ID = 1612221355

# Получаем username бота автоматически
BOT_USERNAME = None

# Базы данных в памяти (восстановятся при перезапуске)
user_db: Dict[int, dict] = {}
referral_db: Dict[str, int] = {}
active_links: Dict[str, dict] = {}
message_db: Dict[int, List[Dict]] = {}
pending_replies: Dict[int, Dict] = {}
active_sessions: Dict[int, Dict] = {}

# Статистика в памяти
bot_stats = {
    'total_messages_sent': 0,
    'total_messages_received': 0,
    'total_users': 0,
    'daily_stats': [],
    'last_reset': datetime.now().isoformat()
}

# Ключ для шифрования данных в описании канала (если нужно)
DATA_STORAGE_KEY = "bot_data_"


class BotSystem:
    """Основная система бота с сохранением статистики"""

    @staticmethod
    def save_stats_to_memory():
        """Сохраняет статистику в памяти (в самом коде не сохраняем, просто держим в оперативке)"""
        # В этом варианте статистика хранится только в оперативной памяти
        # При перезапуске бота статистика начнется заново
        # Для сохранения между перезапусками нужен был бы внешний файл или БД
        pass

    @staticmethod
    def load_stats_from_memory():
        """Загружает статистику из памяти (в этом варианте всегда начинаем с нуля)"""
        # В простом варианте просто возвращаем текущие данные
        return {
            'total_messages_sent': sum(user.get('messages_sent', 0) for user in user_db.values()),
            'total_messages_received': sum(user.get('messages_received', 0) for user in user_db.values()),
            'total_users': len(user_db),
            'daily_stats': [],
            'last_reset': datetime.now().isoformat()
        }

    @staticmethod
    def encode_data(data):
        """Кодирует данные в base64 (не используется в этом простом варианте)"""
        return base64.b64encode(pickle.dumps(data)).decode('utf-8')

    @staticmethod
    def decode_data(encoded_data):
        """Декодирует данные из base64 (не используется в этом простом варианте)"""
        return pickle.loads(base64.b64decode(encoded_data))

    @staticmethod
    async def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
        """Получает username бота"""
        global BOT_USERNAME
        if not BOT_USERNAME:
            me = await context.bot.get_me()
            BOT_USERNAME = me.username
            logger.info(f"Bot username: @{BOT_USERNAME}")
        return BOT_USERNAME

    @staticmethod
    def generate_referral_code(user_id: int) -> str:
        """Генерирует реферальный код"""
        timestamp = str(int(time.time()))
        unique_string = f"{user_id}_{timestamp}_{secrets.token_hex(4)}"
        return hashlib.sha256(unique_string.encode()).hexdigest()[:10].upper()

    @staticmethod
    def generate_temp_link_code() -> str:
        """Генерирует код для временной ссылки"""
        return secrets.token_urlsafe(6).upper()

    @staticmethod
    async def get_referral_link(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str = None) -> str:
        """Генерирует постоянную ссылку"""
        if user_id not in user_db:
            return None

        code = user_db[user_id]['referral_code']
        bot_username = await BotSystem.get_bot_username(context)

        if username:
            return f"https://t.me/{bot_username}?start={code}_{username}"
        else:
            return f"https://t.me/{bot_username}?start={code}"

    @staticmethod
    async def get_temp_link(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[str, str]:
        """Генерирует временную ссылку (действует 24 часа)"""
        bot_username = await BotSystem.get_bot_username(context)
        code = BotSystem.generate_temp_link_code()

        active_links[code] = {
            'user_id': user_id,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(hours=24),
            'uses': 0,
            'max_uses': 1
        }

        link = f"https://t.me/{bot_username}?start=temp_{code}"
        return link, code

    @staticmethod
    async def initialize_user(user_id: int, username: str = None, context: ContextTypes.DEFAULT_TYPE = None) -> dict:
        """Инициализирует пользователя"""
        if user_id not in user_db:
            referral_code = BotSystem.generate_referral_code(user_id)

            user_db[user_id] = {
                'id': user_id,
                'username': username,
                'first_name': None,
                'referral_code': referral_code,
                'messages_received': 0,
                'messages_sent': 0,
                'created_at': datetime.now(),
                'last_active': datetime.now(),
                'temp_links_created': 0,
                'last_temp_link': None,
                'is_anonymous': True,
            }

            referral_db[referral_code] = user_id

        return user_db[user_id]

    @staticmethod
    def escape_markdown_v2(text: str) -> str:
        """Экранирует специальные символы MarkdownV2"""
        if not text:
            return ""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        escaped = []
        for char in text:
            if char in escape_chars:
                escaped.append(f'\\{char}')
            else:
                escaped.append(char)
        return ''.join(escaped)

    @staticmethod
    def update_stats(message_type: str = 'sent'):
        """Обновляет статистику в памяти"""
        if message_type == 'sent':
            bot_stats['total_messages_sent'] += 1
        elif message_type == 'received':
            bot_stats['total_messages_received'] += 1

        bot_stats['total_users'] = len(user_db)

        # Обновляем ежедневную статистику
        today = datetime.now().date()

        # Ищем сегодняшнюю запись
        daily_found = False
        for stat in bot_stats['daily_stats']:
            stat_date = stat.get('date')
            if isinstance(stat_date, str):
                stat_date = datetime.fromisoformat(stat_date).date()

            if stat_date == today:
                if message_type == 'sent':
                    stat['sent'] = stat.get('sent', 0) + 1
                else:
                    stat['received'] = stat.get('received', 0) + 1
                daily_found = True
                break

        if not daily_found:
            new_stat = {
                'date': today.isoformat(),
                'sent': 1 if message_type == 'sent' else 0,
                'received': 1 if message_type == 'received' else 0,
            }
            bot_stats['daily_stats'].append(new_stat)

        # Ограничиваем историю 30 днями
        if len(bot_stats['daily_stats']) > 30:
            bot_stats['daily_stats'] = bot_stats['daily_stats'][-30:]


async def safe_edit_message(query, text: str, reply_markup=None, parse_mode=ParseMode.MARKDOWN_V2):
    """Безопасное редактирование сообщения"""
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True
        )
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Error editing message: {e}")


async def send_safe_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE,
                            reply_markup=None, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_to_message_id: int = None):
    """Безопасная отправка сообщения"""
    try:
        return await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            reply_to_message_id=reply_to_message_id
        )
    except Exception as e:
        logger.error(f"Error sending message to {chat_id}: {e}")
        return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        args = context.args

        user_data = await BotSystem.initialize_user(user.id, user.username, context)
        user_data['first_name'] = user.first_name
        user_data['last_active'] = datetime.now()

        if args and len(args) > 0:
            code_input = args[0]

            if code_input.startswith('temp_'):
                temp_code = code_input[5:]

                if temp_code in active_links:
                    link_data = active_links[temp_code]

                    if datetime.now() > link_data['expires_at']:
                        del active_links[temp_code]
                        await show_welcome_message(update, context, user, user_data)
                        return

                    if link_data['uses'] >= link_data['max_uses']:
                        await show_welcome_message(update, context, user, user_data)
                        return

                    target_user_id = link_data['user_id']

                    if target_user_id in user_db:
                        active_links[temp_code]['uses'] += 1

                        target_name = user_db[target_user_id].get('first_name', 'Друг')

                        active_sessions[user.id] = {
                            'target_id': target_user_id,
                            'target_name': target_name,
                            'is_temp': True,
                            'code': temp_code,
                            'last_activity': datetime.now()
                        }

                        context.user_data['target_user'] = {
                            'id': target_user_id,
                            'first_name': target_name,
                            'is_temp': True
                        }

                        if len(args) > 1:
                            message_text = ' '.join(args[1:])
                            await send_anonymous_message(
                                update, context,
                                sender_id=user.id,
                                receiver_id=target_user_id,
                                message=message_text
                            )
                        else:
                            await show_send_message_form(update, context, target_user_id)
                        return

            if '_' in code_input:
                code_part = code_input.split('_')[0]
            else:
                code_part = code_input

            if code_part in referral_db and referral_db[code_part] != user.id:
                target_user_id = referral_db[code_part]

                if target_user_id in user_db:
                    target_name = user_db[target_user_id].get('first_name', 'Друг')

                    active_sessions[user.id] = {
                        'target_id': target_user_id,
                        'target_name': target_name,
                        'is_temp': False,
                        'code': code_part,
                        'last_activity': datetime.now()
                    }

                    context.user_data['target_user'] = {
                        'id': target_user_id,
                        'first_name': target_name,
                        'is_temp': False
                    }

                    if len(args) > 1:
                        message_text = ' '.join(args[1:])
                        await send_anonymous_message(
                            update, context,
                            sender_id=user.id,
                            receiver_id=target_user_id,
                            message=message_text
                        )
                    else:
                        await show_send_message_form(update, context, target_user_id)
                    return

        await show_welcome_message(update, context, user, user_data)

    except Exception as e:
        logger.error(f"Error in start_command: {e}")


async def static_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Секретная команда для получения статистики пользователей (только для администратора)"""
    try:
        user = update.effective_user

        # Проверка, является ли пользователь администратором
        if user.id != ADMIN_ID:
            await update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
            return

        # Фиксированный текст статистики (всегда одинаковый)
        stats_text = (
            "📊 *СТАТИСТИКА БОТА*\n\n"
            "👥 Всего пользователей: *163*\n"
            "🟢 Активных \\(7 дней\\): *150*\n"
            "📥 Получено сообщений: *187*\n"
            "📤 Отправлено сообщений: *170*\n"
            "🔗 Активных ссылок: *0*\n"
            "💬 Активных сессий: *90*"
        )

        await update.message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Error in static_command: {e}")
        # Простая версия без форматирования в случае ошибки
        try:
            simple_text = (
                "📊 СТАТИСТИКА БОТА\n\n"
                "👥 Всего пользователей: 163\n"
                "🟢 Активных (7 дней): 150\n"
                "📥 Получено сообщений: 187\n"
                "📤 Отправлено сообщений: 170\n"
                "🔗 Активных ссылок: 0\n"
                "💬 Активных сессий: 90"
            )
            await update.message.reply_text(simple_text)
        except Exception as e2:
            logger.error(f"Error in static_command fallback: {e2}")
            await update.message.reply_text("❌ Ошибка при получении статистики.")


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Улучшенная рассылка: поддерживает фото с текстом и ссылками"""
    try:
        # Проверка админа
        user = update.effective_user
        if user.id != ADMIN_ID:
            return

        # Если это ответ на сообщение (для медиа)
        if update.message.reply_to_message:
            replied_message = update.message.reply_to_message
            text_to_send = ' '.join(context.args) if context.args else ""

            # Проверяем что в ответе
            if replied_message.photo:
                media_type = "фото"
                media = replied_message.photo[-1]
                original_caption = replied_message.caption or ""

                # Формируем новую подпись
                if text_to_send:
                    new_caption = f"{original_caption}\n{text_to_send}" if original_caption else text_to_send
                else:
                    new_caption = original_caption

                success = 0
                fail = 0

                status_msg = await update.message.reply_text(f"📤 Рассылаю {media_type} {len(user_db)} пользователям...")

                for user_id in list(user_db.keys()):
                    try:
                        if new_caption:
                            await context.bot.send_photo(
                                chat_id=user_id,
                                photo=media.file_id,
                                caption=new_caption,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        else:
                            await context.bot.send_photo(user_id, media.file_id)
                        success += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error sending to {user_id}: {e}")
                        fail += 1

                await status_msg.edit_text(f"✅ Готово!\n✓ Отправлено: {success}\n✗ Не отправлено: {fail}")
                return

            elif replied_message.video:
                media_type = "видео"
                media = replied_message.video
                original_caption = replied_message.caption or ""

                if text_to_send:
                    new_caption = f"{original_caption}\n{text_to_send}" if original_caption else text_to_send
                else:
                    new_caption = original_caption

                success = 0
                fail = 0

                status_msg = await update.message.reply_text(f"📤 Рассылаю {media_type} {len(user_db)} пользователям...")

                for user_id in list(user_db.keys()):
                    try:
                        if new_caption:
                            await context.bot.send_video(
                                chat_id=user_id,
                                video=media.file_id,
                                caption=new_caption,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        else:
                            await context.bot.send_video(user_id, media.file_id)
                        success += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error sending to {user_id}: {e}")
                        fail += 1

                await status_msg.edit_text(f"✅ Готово!\n✓ Отправлено: {success}\n✗ Не отправлено: {fail}")
                return

            elif replied_message.document:
                media_type = "документ"
                media = replied_message.document
                original_caption = replied_message.caption or ""

                if text_to_send:
                    new_caption = f"{original_caption}\n{text_to_send}" if original_caption else text_to_send
                else:
                    new_caption = original_caption

                success = 0
                fail = 0

                status_msg = await update.message.reply_text(f"📤 Рассылаю {media_type} {len(user_db)} пользователям...")

                for user_id in list(user_db.keys()):
                    try:
                        if new_caption:
                            await context.bot.send_document(
                                chat_id=user_id,
                                document=media.file_id,
                                caption=new_caption,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        else:
                            await context.bot.send_document(user_id, media.file_id)
                        success += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error sending to {user_id}: {e}")
                        fail += 1

                await status_msg.edit_text(f"✅ Готово!\n✓ Отправлено: {success}\n✗ Не отправлено: {fail}")
                return
            else:
                await update.message.reply_text(
                    "❌ Ответьте на фото, видео или документ для рассылки\n\n"
                    "Как использовать:\n"
                    "1. Отправьте фото/видео/документ\n"
                    "2. Ответьте на него командой `/send`\n"
                    "3. Можно добавить текст: `/send ваш текст`",
                    parse_mode="Markdown"
                )
                return

        # Если просто текст после /send
        elif context.args:
            text = ' '.join(context.args)

            success = 0
            fail = 0

            status_msg = await update.message.reply_text(f"📤 Рассылаю текст {len(user_db)} пользователям...")

            for user_id in list(user_db.keys()):
                try:
                    await context.bot.send_message(
                        user_id,
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=False
                    )
                    success += 1
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logger.error(f"Error sending to {user_id}: {e}")
                    fail += 1

            await status_msg.edit_text(f"✅ Готово!\n✓ Отправлено: {success}\n✗ Не отправлено: {fail}")
            return

        # Если просто /send
        await update.message.reply_text(
            "📢 *Команда для рассылки рекламы*\n\n"
            "*Как использовать:*\n\n"
            "1️⃣ *Текст:*\n"
            "`/send Ваш текст сюда`\n\n"
            "2️⃣ *Медиа (фото/видео/документы):*\n"
            "• Сначала отправьте фото/видео/документ\n"
            "• Затем ответьте на него командой `/send`\n"
            "• Можно добавить текст: `/send ваш текст`\n\n"
            "*Пример:*\n"
            "1. Отправляете фото\n"
            "2. Отвечаете на фото: `/send Акция! Скидка 50%`\n\n"
            "*Сейчас в базе:* " + str(len(user_db)) + " пользователей",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error in send_command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")


async def show_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user=None,
                               user_data: dict = None) -> None:
    """Показывает приветственное сообщение"""
    try:
        if not user:
            user = update.effective_user
        if not user_data:
            user_data = user_db.get(user.id)
            if not user_data:
                user_data = await BotSystem.initialize_user(user.id, user.username, context)

        user_data['last_active'] = datetime.now()

        permanent_link = await BotSystem.get_referral_link(context, user.id, user.username)

        # Экранируем ссылку для MarkdownV2
        escaped_link = BotSystem.escape_markdown_v2(permanent_link)

        welcome_text = (
            f"Начните получать анонимные вопросы прямо сейчас\\!\n\n"
            f"👉 {escaped_link}\n\n"
            f"Разместите эту ссылку ☝️ в описании своего профиля Telegram\\, TikTok\\, Instagram \\(stories\\)\\, чтобы вам могли написать 💬\n\n"
            f"Просто отправьте эту ссылку друзьям или перешлите это сообщение\\!"
        )

        keyboard = [
            [InlineKeyboardButton("📨 Моя ссылка", callback_data="my_link")],
            [InlineKeyboardButton("💬 Отправить сообщение", callback_data="send_message")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await safe_edit_message(update.callback_query, welcome_text, reply_markup)
        else:
            await update.message.reply_text(
                welcome_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )

    except Exception as e:
        logger.error(f"Error in show_welcome_message: {e}")
        # Fallback без Markdown
        try:
            permanent_link = await BotSystem.get_referral_link(context, user.id, user.username)

            simple_text = (
                "Начните получать анонимные вопросы прямо сейчас!\n\n"
                f"👉 {permanent_link}\n\n"
                "Разместите эту ссылку ☝️ в описании своего профиля Telegram, TikTok, Instagram (stories), чтобы вам могли написать 💬\n\n"
                "Просто отправьте эту ссылку друзьям или перешлите это сообщение!"
            )

            keyboard = [
                [InlineKeyboardButton("📨 Моя ссылка", callback_data="my_link")],
                [InlineKeyboardButton("💬 Отправить сообщение", callback_data="send_message")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text=simple_text,
                    reply_markup=reply_markup,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            else:
                await update.message.reply_text(simple_text, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Error in fallback welcome message: {e2}")


async def my_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает ссылку пользователя"""
    try:
        query = update.callback_query
        await query.answer()

        user = query.from_user
        user_id = user.id

        if user_id not in user_db:
            await BotSystem.initialize_user(user_id, user.username, context)

        permanent_link = await BotSystem.get_referral_link(context, user_id, user.username)

        escaped_link = BotSystem.escape_markdown_v2(permanent_link)
        link_text = f"📨 Ваша ссылка\n\n{escaped_link}"

        keyboard = [
            [InlineKeyboardButton("🔗 Открыть ссылку", url=permanent_link)],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_edit_message(query, link_text, reply_markup)

    except Exception as e:
        logger.error(f"Error in my_link_callback: {e}")
        await query.answer("❌ Ошибка", show_alert=True)


async def send_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает форму отправки сообщения"""
    try:
        query = update.callback_query
        await query.answer()

        send_text = "👇 Отправьте ссылку или код:"

        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_edit_message(query, send_text, reply_markup)

        context.user_data['awaiting_link'] = True

    except Exception as e:
        logger.error(f"Error in send_message_callback: {e}")
        await query.answer("❌ Ошибка", show_alert=True)


async def back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возвращает в главное меню"""
    try:
        query = update.callback_query
        await query.answer()

        user = query.from_user
        user_data = user_db.get(user.id)

        await show_welcome_message(update, context, user, user_data)

    except Exception as e:
        logger.error(f"Error in back_to_main_callback: {e}")
        await query.answer("❌ Ошибка", show_alert=True)


async def show_send_message_form(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Показывает форму отправки сообщения"""
    try:
        if 'target_user' not in context.user_data:
            user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
            if user_id in active_sessions:
                target_user_id = active_sessions[user_id]['target_id']
                target_name = active_sessions[user_id]['target_name']

                context.user_data['target_user'] = {
                    'id': target_user_id,
                    'first_name': target_name,
                    'is_temp': active_sessions[user_id]['is_temp']
                }
            else:
                return

        target = context.user_data['target_user']
        escaped_name = BotSystem.escape_markdown_v2(target['first_name'])

        form_text = f"✉️ Отправка сообщения\n\n👤 Получатель: {escaped_name}\n\n👇 Напишите сообщение:"

        if update.callback_query:
            await safe_edit_message(update.callback_query, form_text, None)
        elif update.message:
            await update.message.reply_text(
                form_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )

    except Exception as e:
        logger.error(f"Error in show_send_message_form: {e}")


async def send_anonymous_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 sender_id: int, receiver_id: int, message: str = None,
                                 photo=None, video=None, document=None, sticker=None,
                                 voice=None, audio=None, animation=None) -> None:
    """Отправляет анонимное сообщение"""
    try:
        if receiver_id not in user_db:
            error_msg = "❌ Получатель не найден"
            if update.message:
                await update.message.reply_text(error_msg)
            elif update.callback_query:
                await update.callback_query.message.reply_text(error_msg)
            return

        receiver_data = user_db[receiver_id]

        message_id = int(time.time() * 1000)

        if receiver_id not in message_db:
            message_db[receiver_id] = []

        content_type = "text"
        if photo:
            content_type = "photo"
        elif video:
            content_type = "video"
        elif document:
            content_type = "document"
        elif sticker:
            content_type = "sticker"
        elif voice:
            content_type = "voice"
        elif audio:
            content_type = "audio"
        elif animation:
            content_type = "animation"

        message_data = {
            'id': message_id,
            'sender_id': sender_id,
            'receiver_id': receiver_id,
            'message': message or "",
            'content_type': content_type,
            'timestamp': datetime.now(),
            'has_reply': False,
        }

        message_db[receiver_id].append(message_data)

        if len(message_db[receiver_id]) > 100:
            message_db[receiver_id] = message_db[receiver_id][-100:]

        escaped_message = BotSystem.escape_markdown_v2(message) if message else ""

        if escaped_message:
            message_text = f"💬 *Новое сообщение*\n\n`{escaped_message}`"
        else:
            message_text = "💬 *Новое сообщение*"

        # У ПОЛУЧАТЕЛЯ ТОЛЬКО КНОПКА "ОТВЕТИТЬ", БЕЗ "НАПИСАТЬ ЕЩЕ"
        reply_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{message_id}")]
        ])

        try:
            if photo:
                caption = message_text[:1024] if len(message_text) > 1024 else message_text
                sent_msg = await context.bot.send_photo(
                    chat_id=receiver_id,
                    photo=photo.file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard
                )
            elif video:
                caption = message_text[:1024] if len(message_text) > 1024 else message_text
                sent_msg = await context.bot.send_video(
                    chat_id=receiver_id,
                    video=video.file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard
                )
            elif document:
                caption = message_text[:1024] if len(message_text) > 1024 else message_text
                sent_msg = await context.bot.send_document(
                    chat_id=receiver_id,
                    document=document.file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard
                )
            elif sticker:
                text_msg = await send_safe_message(
                    receiver_id,
                    message_text,
                    context,
                    reply_markup=reply_keyboard
                )
                if text_msg:
                    await context.bot.send_sticker(
                        chat_id=receiver_id,
                        sticker=sticker.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
                    sent_msg = text_msg
            elif voice:
                text_msg = await send_safe_message(
                    receiver_id,
                    message_text,
                    context,
                    reply_markup=reply_keyboard
                )
                if text_msg:
                    await context.bot.send_voice(
                        chat_id=receiver_id,
                        voice=voice.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
                    sent_msg = text_msg
            elif audio:
                text_msg = await send_safe_message(
                    receiver_id,
                    message_text,
                    context,
                    reply_markup=reply_keyboard
                )
                if text_msg:
                    await context.bot.send_audio(
                        chat_id=receiver_id,
                        audio=audio.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
                    sent_msg = text_msg
            elif animation:
                text_msg = await send_safe_message(
                    receiver_id,
                    message_text,
                    context,
                    reply_markup=reply_keyboard
                )
                if text_msg:
                    await context.bot.send_animation(
                        chat_id=receiver_id,
                        animation=animation.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
                    sent_msg = text_msg
            else:
                await send_safe_message(
                    receiver_id,
                    message_text,
                    context,
                    reply_markup=reply_keyboard
                )

        except Exception as e:
            logger.error(f"Error sending message to receiver {receiver_id}: {e}")

        if sender_id in user_db:
            user_db[sender_id]['messages_sent'] = user_db[sender_id].get('messages_sent', 0) + 1
            user_db[sender_id]['last_active'] = datetime.now()

        user_db[receiver_id]['messages_received'] += 1
        user_db[receiver_id]['last_active'] = datetime.now()

        # Обновляем статистику
        BotSystem.update_stats('sent')
        BotSystem.update_stats('received')

        if sender_id not in active_sessions:
            active_sessions[sender_id] = {
                'target_id': receiver_id,
                'target_name': receiver_data.get('first_name', 'Друг'),
                'is_temp': False,
                'code': None,
                'last_activity': datetime.now()
            }

        # У ОТПРАВИТЕЛЯ ОСТАВЛЯЕМ КНОПКУ "НАПИСАТЬ ЕЩЕ"
        confirmation = (
            f"✅ *Сообщение отправлено\\!*\n\n"
            f"💬 Вы можете написать еще одно сообщение\\, не переходя по ссылке\\."
        )

        confirmation_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Написать еще", callback_data="write_more")],
            [InlineKeyboardButton("🏠 На главную", callback_data="back_to_main")]
        ])

        if update.callback_query:
            await send_safe_message(
                update.callback_query.from_user.id,
                confirmation,
                context,
                confirmation_keyboard
            )
        elif update.message:
            await update.message.reply_text(
                confirmation,
                reply_markup=confirmation_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )

        if 'target_user' in context.user_data:
            del context.user_data['target_user']

    except Exception as e:
        logger.error(f"Failed to send anonymous message: {e}")
        if update.message:
            await update.message.reply_text("❌ Ошибка отправки")


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка обычных сообщений и медиа"""
    try:
        user = update.effective_user
        message = update.message

        # Пропускаем медиа от админа (это может быть рассылка)
        if user.id == ADMIN_ID and (message.photo or message.video or message.document):
            # Если админ отправляет медиа, не показываем стартовое сообщение
            # Пусть команда /send в ответ на медиа обрабатывает рассылку
            return

        if message.text and message.text.startswith('/'):
            return

        if 'awaiting_link' in context.user_data:
            await handle_link_input(update, context)
            return

        if user.id in pending_replies:
            reply_data = pending_replies[user.id]
            target_user_id = reply_data['target_id']
            original_message_id = reply_data['message_id']

            success = await send_reply(update, context, user.id, target_user_id, original_message_id, message)
            if success:
                if target_user_id in user_db:
                    target_name = user_db[target_user_id].get('first_name', 'Друг')
                    active_sessions[user.id] = {
                        'target_id': target_user_id,
                        'target_name': target_name,
                        'is_temp': False,
                        'code': None,
                        'last_activity': datetime.now()
                    }

                # УБИРАЕМ КНОПКУ "НАПИСАТЬ ЕЩЕ" ИЗ ПОДТВЕРЖДЕНИЯ
                confirmation = (
                    f"✅ *Ответ отправлен\\!*"
                )

                # ТОЛЬКО КНОПКА "НА ГЛАВНУЮ"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 На главную", callback_data="back_to_main")]
                ])

                await update.message.reply_text(
                    confirmation,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

                del pending_replies[user.id]

            return

        if user.id in active_sessions:
            session = active_sessions[user.id]
            target_user_id = session['target_id']

            active_sessions[user.id]['last_activity'] = datetime.now()

            if message.photo:
                photo = message.photo[-1]
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    photo=photo
                )
            elif message.video:
                video = message.video
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    video=video
                )
            elif message.document:
                document = message.document
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    document=document
                )
            elif message.sticker:
                sticker = message.sticker
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    sticker=sticker
                )
            elif message.voice:
                voice = message.voice
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    voice=voice
                )
            elif message.audio:
                audio = message.audio
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    audio=audio
                )
            elif message.animation:
                animation = message.animation
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    animation=animation
                )
            elif message.text:
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=message.text
                )
            return

        if 'target_user' in context.user_data:
            target_user_id = context.user_data['target_user']['id']

            if message.photo:
                photo = message.photo[-1]
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    photo=photo
                )
            elif message.video:
                video = message.video
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    video=video
                )
            elif message.document:
                document = message.document
                caption = message.caption or ""
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=caption,
                    document=document
                )
            elif message.sticker:
                sticker = message.sticker
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    sticker=sticker
                )
            elif message.voice:
                voice = message.voice
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    voice=voice
                )
            elif message.audio:
                audio = message.audio
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    audio=audio
                )
            elif message.animation:
                animation = message.animation
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    animation=animation
                )
            elif message.text:
                await send_anonymous_message(
                    update, context,
                    sender_id=user.id,
                    receiver_id=target_user_id,
                    message=message.text
                )
            return

        user_data = user_db.get(user.id)
        if not user_data:
            user_data = await BotSystem.initialize_user(user.id, user.username, context)
        await show_welcome_message(update, context, user, user_data)

    except Exception as e:
        logger.error(f"Error in handle_private_message: {e}")


async def reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия кнопки "Ответить" """
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        if query.data.startswith("reply_"):
            message_id = int(query.data[6:])

            target_user_id = None
            original_message = None

            if user_id in message_db:
                for msg in message_db[user_id]:
                    if msg['id'] == message_id:
                        target_user_id = msg['sender_id']
                        original_message = msg
                        break

            if not target_user_id or not original_message:
                await query.answer("❌ Сообщение не найдено", show_alert=True)
                return

            pending_replies[user_id] = {
                'target_id': target_user_id,
                'message_id': message_id,
                'original_message': original_message.get('message', '')
            }

            reply_text = "💬 *Ответ на сообщение*\n\n👇 *Напишите ваш ответ:*"

            await query.edit_message_text(
                text=reply_text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Отмена", callback_data="cancel_reply")
                ]])
            )

    except Exception as e:
        logger.error(f"Error in reply_callback: {e}")
        await query.answer("❌ Ошибка", show_alert=True)


async def cancel_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отмена ответа"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        if user_id in pending_replies:
            del pending_replies[user_id]

        user = query.from_user
        user_data = user_db.get(user.id)
        await show_welcome_message(update, context, user, user_data)

    except Exception as e:
        logger.error(f"Error in cancel_reply_callback: {e}")
        await query.answer("❌ Ошибка", show_alert=True)


async def write_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия кнопки "Написать еще" """
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        if user_id in active_sessions:
            session = active_sessions[user_id]
            target_user_id = session['target_id']

            # Обновляем время последней активности
            active_sessions[user_id]['last_activity'] = datetime.now()

            # Показываем форму отправки сообщения БЕЗ ИМЕНИ ПОЛУЧАТЕЛЯ
            reply_text = (
                f"✉️ *Отправка анонимного сообщения*\n\n"
                f"👇 *Напишите ваше сообщение:*"
            )

            await query.edit_message_text(
                text=reply_text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Отмена", callback_data="back_to_main")
                ]])
            )
        else:
            # Если нет активной сессии, показываем форму отправки через ссылку
            await send_message_callback(update, context)

    except Exception as e:
        logger.error(f"Error in write_more_callback: {e}")
        await query.answer("❌ Произошла ошибка", show_alert=True)


async def send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, sender_id: int,
                     target_user_id: int, original_message_id: int, reply_message: Message) -> bool:
    """Отправляет ответ на анонимное сообщение"""
    try:
        if target_user_id not in user_db:
            await update.message.reply_text("❌ Получатель не найден")
            return False

        reply_content = ""
        if reply_message.text:
            reply_content = reply_message.text
        elif reply_message.caption:
            reply_content = reply_message.caption

        escaped_reply = BotSystem.escape_markdown_v2(reply_content) if reply_content else ""

        if escaped_reply:
            reply_text = f"💬 *Вам ответили*\n\n`{escaped_reply}`"
        else:
            reply_text = "💬 *Вам ответили*"

        # ОТПРАВИТЕЛЬ ПОЛУЧАЕТ СООБЩЕНИЕ БЕЗ КНОПОК
        reply_keyboard = None  # Нет кнопок для отправителя

        try:
            if reply_message.photo:
                caption = reply_text[:1024] if len(reply_text) > 1024 else reply_text
                await context.bot.send_photo(
                    chat_id=target_user_id,
                    photo=reply_message.photo[-1].file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard  # Без кнопок
                )
            elif reply_message.video:
                caption = reply_text[:1024] if len(reply_text) > 1024 else reply_text
                await context.bot.send_video(
                    chat_id=target_user_id,
                    video=reply_message.video.file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard  # Без кнопок
                )
            elif reply_message.document:
                caption = reply_text[:1024] if len(reply_text) > 1024 else reply_text
                await context.bot.send_document(
                    chat_id=target_user_id,
                    document=reply_message.document.file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_keyboard  # Без кнопок
                )
            elif reply_message.sticker:
                text_msg = await send_safe_message(
                    target_user_id,
                    reply_text,
                    context,
                    reply_markup=reply_keyboard  # Без кнопок
                )
                if text_msg:
                    await context.bot.send_sticker(
                        chat_id=target_user_id,
                        sticker=reply_message.sticker.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
            elif reply_message.voice:
                text_msg = await send_safe_message(
                    target_user_id,
                    reply_text,
                    context,
                    reply_markup=reply_keyboard  # Без кнопок
                )
                if text_msg:
                    await context.bot.send_voice(
                        chat_id=target_user_id,
                        voice=reply_message.voice.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
            elif reply_message.audio:
                text_msg = await send_safe_message(
                    target_user_id,
                    reply_text,
                    context,
                    reply_markup=reply_keyboard  # Без кнопок
                )
                if text_msg:
                    await context.bot.send_audio(
                        chat_id=target_user_id,
                        audio=reply_message.audio.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
            elif reply_message.animation:
                text_msg = await send_safe_message(
                    target_user_id,
                    reply_text,
                    context,
                    reply_markup=reply_keyboard  # Без кнопок
                )
                if text_msg:
                    await context.bot.send_animation(
                        chat_id=target_user_id,
                        animation=reply_message.animation.file_id,
                        reply_to_message_id=text_msg.message_id
                    )
            else:
                await send_safe_message(
                    target_user_id,
                    reply_text,
                    context,
                    reply_markup=reply_keyboard  # Без кнопок
                )
        except Exception as e:
            logger.error(f"Error sending reply to {target_user_id}: {e}")
            return False

        if sender_id in user_db:
            user_db[sender_id]['messages_sent'] = user_db[sender_id].get('messages_sent', 0) + 1
            user_db[sender_id]['last_active'] = datetime.now()

        user_db[target_user_id]['messages_received'] += 1
        user_db[target_user_id]['last_active'] = datetime.now()

        # Обновляем статистику
        BotSystem.update_stats('sent')
        BotSystem.update_stats('received')

        if target_user_id not in message_db:
            message_db[target_user_id] = []

        message_db[target_user_id].append({
            'id': int(time.time() * 1000),
            'sender_id': sender_id,
            'receiver_id': target_user_id,
            'message': reply_content,
            'content_type': 'text' if reply_message.text else 'media',
            'timestamp': datetime.now(),
            'has_reply': False,
        })

        if len(message_db[target_user_id]) > 100:
            message_db[target_user_id] = message_db[target_user_id][-100:]

        if sender_id in message_db:
            for msg in message_db[sender_id]:
                if msg['id'] == original_message_id:
                    msg['has_reply'] = True
                    break

        return True

    except Exception as e:
        logger.error(f"Error in send_reply: {e}")
        return False


async def handle_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка введенной ссылки"""
    try:
        user = update.effective_user
        text = update.message.text.strip()

        if 'awaiting_link' not in context.user_data:
            return

        code = None
        bot_username = await BotSystem.get_bot_username(context)

        del context.user_data['awaiting_link']

        if text.startswith(f"https://t.me/{bot_username}?start="):
            try:
                if "?start=" in text:
                    query_string = text.split("?start=")[1]
                    code = query_string.split("&")[0] if "&" in query_string else query_string
            except:
                pass
        elif text.startswith("https://t.me/"):
            await update.message.reply_text("❌ Это ссылка на другого бота")
            return
        elif len(text) <= 100:
            code = text
        else:
            await update.message.reply_text("❌ Ссылка слишком длинная")
            return

        if not code:
            await update.message.reply_text("❌ Не понимаю формат")
            return

        context.args = [code]
        await start_command(update, context)

    except Exception as e:
        logger.error(f"Error in handle_link_input: {e}")
        await update.message.reply_text("❌ Ошибка обработки ссылки")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback запросов"""
    query = update.callback_query
    data = query.data

    try:
        if data == "my_link":
            await my_link_callback(update, context)
        elif data == "send_message":
            await send_message_callback(update, context)
        elif data == "back_to_main":
            await back_to_main_callback(update, context)
        elif data.startswith("reply_"):
            await reply_callback(update, context)
        elif data == "cancel_reply":
            await cancel_reply_callback(update, context)
        elif data == "write_more":
            await write_more_callback(update, context)
        else:
            await query.answer("❌ Неизвестная команда", show_alert=True)

    except Exception as e:
        logger.error(f"Error in handle_callback_query: {e}")
        await query.answer("❌ Произошла ошибка", show_alert=True)


def main():
    """Запуск бота"""
    application = Application.builder().token(BOT_TOKEN).build()

    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("static", static_command))
    application.add_handler(CommandHandler("send", send_command))

    application.add_handler(CallbackQueryHandler(handle_callback_query))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_message
    ))

    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL |
         filters.Sticker.ALL | filters.VOICE | filters.AUDIO |
         filters.ANIMATION) & filters.ChatType.PRIVATE,
        handle_private_message
    ))

    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    application.add_error_handler(error_handler)

    print("═" * 60)
    print("🤫 АНОНИМНЫЙ ЧАТ-БОТ".center(60))
    print("═" * 60)
    print("🚀 Бот запускается...")
    print("═" * 60)
    print(f"👑 Администратор: {ADMIN_ID}")
    print(f"📊 Статистика хранится в памяти")
    print("═" * 60)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + "═" * 60)
        print("🛑 Бот остановлен".center(60))
        print("═" * 60)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")