import logging
import requests
import json
import re
import os
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode

# === ДОБАВЛЕНО для Render: поток и веб-сервер ===
import threading
from flask import Flask

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
ADMIN_ID = 7211675735
TELEGRAM_TOKEN = '8242776474:AAGN5Crcnu2O46GgjJs6cLbJWRIDG9cEJqc'
DEEPSEEK_API_KEY = 'io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6ImUxYmMyYjJhLWYxOTQtNDgxNi04NWM2LTNhMWUzMWRiODM5OSIsImV4cCI6NDkwODM1OTQyN30.A3bDGQ_uTwtZt-hCTkvFRKbN-7344vRtqwYF3VyHjkDdpfFAWaulYF4kt4O0P06N_LmFeGBcmgcsJvPpz0YSMQ'
DEEPSEEK_API_URL = "https://api.intelligence.io.solutions/api/v1/chat/completions"
DEEPSEEK_MODEL_NAME = "deepseek-ai/DeepSeek-R1-0528"
STYLES_FILE = 'styles.json'
ADMINS_FILE = 'admins.json'

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
STYLES = {}
CURRENT_STYLE_KEY = 'gopnik'
BOT_IS_ACTIVE = True
bot_stats = {"requests": 0, "errors": 0}
ADMINS = {}
chat_histories = defaultdict(list)

# --- Состояния для ConversationHandler ---
GET_STYLE_NAME, GET_STYLE_PROMPT, GET_EDIT_PROMPT = range(3)
GET_ADMIN_FORWARD = range(3, 4)[0]

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def escape_markdown(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

def md2(text):
    return escape_markdown(str(text))

def load_data():
    global STYLES, CURRENT_STYLE_KEY, ADMINS
    if os.path.exists(STYLES_FILE):
        with open(STYLES_FILE, 'r', encoding='utf-8') as f:
            STYLES = json.load(f)
    else:
        STYLES = {'gopnik': "Отвечай как гопник и на пох"}
        save_styles()
    if CURRENT_STYLE_KEY not in STYLES:
        CURRENT_STYLE_KEY = next(iter(STYLES)) if STYLES else None
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                ADMINS = {int(k): v for k, v in data.items()}
            else:
                ADMINS = {}
        except (json.JSONDecodeError, AttributeError):
            ADMINS = {}
    if ADMIN_ID not in ADMINS:
        ADMINS[ADMIN_ID] = f"user_{ADMIN_ID}"
        save_admins()

def save_styles():
    with open(STYLES_FILE, 'w', encoding='utf-8') as f:
        json.dump(STYLES, f, ensure_ascii=False, indent=4)

def save_admins():
    with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
        json.dump(ADMINS, f, ensure_ascii=False, indent=4)

def get_model_response(prompt: str, system_prompt_override=None) -> str:
    global bot_stats
    bot_stats["requests"] += 1
    headers = {
        'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
        'Content-Type': 'application/json'
    }
    system_prompt = system_prompt_override if system_prompt_override else STYLES.get(
        CURRENT_STYLE_KEY, "You are a helpful assistant."
    )
    payload = {
        "model": DEEPSEEK_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, data=json.dumps(payload), timeout=60)
        if response.status_code == 200:
            response_data = response.json()
            raw_content = response_data["choices"][0]["message"]["content"]
            return re.sub(r"<think>.*?</think>\s*", "", raw_content, flags=re.DOTALL).strip()
        else:
            bot_stats["errors"] += 1
            error_details = response.text
            logger.error(f"API Deepseek вернуло ошибку {response.status_code}: {error_details}")
            return f"Слышь, сервак Deepseek ответил ошибкой {response.status_code}."
    except Exception as e:
        bot_stats["errors"] += 1
        logger.error(f"Произошла ошибка при запросе к Deepseek: {e}")
        return "Короче, во мне какая-то дичь сломалась."

async def query_or_message(update, text, **kwargs):
    if update.callback_query:
        await update.callback_query.edit_message_text(text, **kwargs)
    else:
        await update.message.reply_text(text, **kwargs)

def generate_main_admin_keyboard() -> InlineKeyboardMarkup:
    status_text = "✅ Включен" if BOT_IS_ACTIVE else "🚫 Выключен"
    keyboard = [
        [InlineKeyboardButton("👑 Управление Админами", callback_data='menu_admins')],
        [InlineKeyboardButton("🎨 Управление стилями", callback_data='menu_style')],
        [
            InlineKeyboardButton(f"🚦 Состояние: {status_text}", callback_data='toggle_bot_status'),
            InlineKeyboardButton("📊 Статистика", callback_data='show_stats')
        ],
        [InlineKeyboardButton("❌ Закрыть", callback_data='close_panel')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def render_styles_list(query):
    keyboard = []
    if not STYLES:
        keyboard.append([InlineKeyboardButton("Стилей пока нет. Добавьте первый!", callback_data='add_style_start')])
    else:
        for key in STYLES:
            button_text = f"👉 {key}" if key == CURRENT_STYLE_KEY else key
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'manage_style_{key}')])
    keyboard.append([InlineKeyboardButton("« Назад", callback_data='menu_style')])
    await query.edit_message_text(
        text=md2("🎨 Стили оформления\n\nВыберите стиль для управления:"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def show_style_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("📋 Список стилей", callback_data='list_styles')],
        [InlineKeyboardButton("➕ Добавить стиль", callback_data='add_style_start')],
        [InlineKeyboardButton("« Назад", callback_data='open_admin_panel')]
    ]
    await query_or_message(update, md2("🎨 Управление стилями"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def show_admin_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("📋 Список админов", callback_data='list_admins')],
        [InlineKeyboardButton("➕ Добавить админа", callback_data='add_admin_start')],
        [InlineKeyboardButton("« Назад", callback_data='open_admin_panel')]
    ]
    await query_or_message(update, md2("👑 Управление администраторами"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def add_style_start(update, context):
    await query_or_message(
        update,
        md2("✏️ Введите короткое название для нового стиля (латиницей, без пробелов, например: `my_style`):"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return GET_STYLE_NAME

async def get_style_name(update, context):
    style_key = update.message.text.strip()
    if not re.match(r'^[a-zA-Z0-9_]+$', style_key):
        await update.message.reply_text(md2("❗ Только латиница, цифры и _. Попробуйте снова или напишите /cancel."), parse_mode=ParseMode.MARKDOWN_V2)
        return GET_STYLE_NAME
    if style_key in STYLES:
        await update.message.reply_text(md2("❗ Такой стиль уже есть. Придумайте другое имя или напишите /cancel."), parse_mode=ParseMode.MARKDOWN_V2)
        return GET_STYLE_NAME
    context.user_data['new_style_key'] = style_key
    await update.message.reply_text(
        md2("✏️ Теперь пришлите полный текст системного промпта для этого стиля:"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return GET_STYLE_PROMPT

async def get_style_prompt(update, context):
    style_key = context.user_data['new_style_key']
    STYLES[style_key] = update.message.text
    save_styles()
    await update.message.reply_text(
        md2(f"✅ Стиль `{style_key}` успешно добавлен!"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await show_style_menu(update, context)
    context.user_data.clear()
    return ConversationHandler.END

async def edit_style_start(update, context):
    style_key = context.user_data['edit_style_key']
    await query_or_message(
        update,
        md2(f"✏️ Пришлите новый системный промпт для стиля `{style_key}`:"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return GET_EDIT_PROMPT

async def get_edit_prompt(update, context):
    style_key = context.user_data['edit_style_key']
    STYLES[style_key] = update.message.text
    save_styles()
    await update.message.reply_text(
        md2(f"✅ Стиль `{style_key}` успешно обновлен!"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await show_style_menu(update, context)
    context.user_data.clear()
    return ConversationHandler.END

async def edit_style_entry(update, context):
    key = update.callback_query.data.replace('edit_style_', '')
    if key not in STYLES:
        await update.callback_query.answer("Стиль не найден", show_alert=True)
        return ConversationHandler.END
    context.user_data['edit_style_key'] = key
    return await edit_style_start(update, context)

async def add_admin_start(update, context):
    await query_or_message(
        update,
        md2("👤 Перешлите любое сообщение от пользователя, которого хотите сделать админом. Для отмены напишите /cancel"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return GET_ADMIN_FORWARD

async def get_admin_from_forward(update, context):
    origin = update.message.forward_origin
    if not origin or origin.type != 'user':
        await update.message.reply_text(
            md2("❗ Это не пересланное сообщение. Попробуйте еще раз или напишите /cancel.\n\n(Примечание: не сработает, если у пользователя стоит запрет на пересылку)."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return GET_ADMIN_FORWARD
    new_admin = origin.sender_user
    new_admin_id = new_admin.id
    new_admin_username = new_admin.username or f"user_{new_admin_id}"
    if new_admin_id in ADMINS:
        await update.message.reply_text(md2(f"Пользователь @{new_admin_username} уже админ."), parse_mode=ParseMode.MARKDOWN_V2)
    else:
        ADMINS[new_admin_id] = new_admin_username
        save_admins()
        await update.message.reply_text(md2(f"✅ Пользователь @{new_admin_username} назначен админом."), parse_mode=ParseMode.MARKDOWN_V2)
    await show_admin_menu(update, context)
    return ConversationHandler.END

async def incorrect_admin_input(update, context):
    await update.message.reply_text(md2("❗ Неверный ввод. Пожалуйста, ПЕРЕШЛИТЕ мне сообщение или напишите /cancel."), parse_mode=ParseMode.MARKDOWN_V2)
    return GET_ADMIN_FORWARD

async def cancel_conversation(update, context):
    context.user_data.clear()
    await update.message.reply_text(md2("❌ Действие отменено."), parse_mode=ParseMode.MARKDOWN_V2)
    await admin_panel_command(update, context)
    return ConversationHandler.END

async def start(update, context):
    user = update.effective_user
    if user.id == ADMIN_ID and (user.username and ADMINS.get(ADMIN_ID) != user.username):
        ADMINS[ADMIN_ID] = user.username
        save_admins()
    base_text = (
        "🤖 AI DeepSeek Бот\n"
        "В личке отвечаю на любой вопрос. В группе — через 'Дип, [твой вопрос]'.\n\n"
        "ℹ️ Чтобы узнать интересный факт об ИИ, используй команду /fact."
    )
    if user.id in ADMINS:
        keyboard = [[InlineKeyboardButton("👑 Открыть админ-панель", callback_data='open_admin_panel')]]
        await update.message.reply_text(f"{base_text}\n\nДля тебя, босс, спец-кнопка:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(base_text)

async def admin_panel_command(update, context):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text(md2("❗ Э, ты не админ, не лезь."), parse_mode=ParseMode.MARKDOWN_V2)
        return
    await update.message.reply_text("Админ-панель:", reply_markup=generate_main_admin_keyboard())

async def ai_fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Ищу интересный факт...")
    prompt = (
        "Расскажи один очень интересный и малоизвестный факт или короткую историю про искусственный интеллект. "
        "Ответ должен быть не длиннее 5-6 предложений."
    )
    fact = get_model_response(prompt, system_prompt_override="Ты — эрудированный историк технологий.")
    await update.message.reply_text(fact)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    global CURRENT_STYLE_KEY, BOT_IS_ACTIVE

    if data == 'open_admin_panel':
        await query.edit_message_text("Админ-панель:", reply_markup=generate_main_admin_keyboard())
    elif data == 'toggle_bot_status':
        BOT_IS_ACTIVE = not BOT_IS_ACTIVE
        status_message = "✅ Бот включен." if BOT_IS_ACTIVE else "🚫 Бот выключен."
        await query.answer(status_message, show_alert=True)
        await query.edit_message_reply_markup(reply_markup=generate_main_admin_keyboard())
    elif data == 'menu_style':
        await show_style_menu(update, context)
    elif data == 'menu_admins':
        await show_admin_menu(update, context)
    elif data == 'list_styles':
        await render_styles_list(query)
    elif data.startswith('manage_style_'):
        key = data.replace('manage_style_', '')
        if key not in STYLES:
            await query.answer("Стиль не найден", show_alert=True)
            return
        escaped_key = md2(key)
        prompt_block = STYLES[key]
        text = f"🎨 Управление стилем `{escaped_key}`\n\n```\n{prompt_block}\n```"
        keyboard = [
            [InlineKeyboardButton("✅ Выбрать этот стиль", callback_data=f'set_style_{key}')],
            [InlineKeyboardButton("✏️ Редактировать промпт", callback_data=f'edit_style_{key}')],
            [InlineKeyboardButton("🗑️ Удалить стиль", callback_data=f'delete_style_{key}')],
            [InlineKeyboardButton("« К списку", callback_data='list_styles')]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    elif data.startswith('set_style_'):
        CURRENT_STYLE_KEY = data.replace('set_style_', '')
        await query.answer(f"Стиль '{CURRENT_STYLE_KEY}' активирован!")
        await render_styles_list(query)
    elif data.startswith('delete_style_'):
        key_to_delete = data.replace('delete_style_', '')
        keyboard = [
            [
                InlineKeyboardButton(f"ДА, УДАЛИТЬ '{key_to_delete}'", callback_data=f'confirm_delete_{key_to_delete}'),
                InlineKeyboardButton("НЕТ, ОТМЕНА", callback_data='list_styles')
            ]
        ]
        await query.edit_message_text(
            text=md2(f"❗ Вы уверены, что хотите удалить стиль `{key_to_delete}`?"),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    elif data.startswith('confirm_delete_'):
        key_to_delete = data.replace('confirm_delete_', '')
        if key_to_delete in STYLES:
            del STYLES[key_to_delete]
            save_styles()
        if CURRENT_STYLE_KEY == key_to_delete:
            CURRENT_STYLE_KEY = next(iter(STYLES)) if STYLES else None
        await query.answer("Стиль удален.")
        await render_styles_list(query)
    elif data == 'list_admins':
        lines = ["👑 Список администраторов:\n"]
        for admin_id, username in ADMINS.items():
            name = md2(username)
            crown = md2(" (главный)") if admin_id == ADMIN_ID else ""
            lines.append(f"\\- @{name}{crown}\n")
        text = "".join(lines)
        keyboard = [
            [InlineKeyboardButton("➖ Удалить админа", callback_data='delete_admin_list')],
            [InlineKeyboardButton("« Назад", callback_data='menu_admins')]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    elif data == 'delete_admin_list':
        keyboard = []
        for admin_id, username in ADMINS.items():
            if admin_id != ADMIN_ID:
                keyboard.append([InlineKeyboardButton(f"🗑️ @{username}", callback_data=f'delete_admin_{admin_id}')])
        if len(keyboard) == 0:
            keyboard.append([InlineKeyboardButton("Кроме вас, админов нет", callback_data='no_op')])
        keyboard.append([InlineKeyboardButton("« Назад", callback_data='list_admins')])
        await query.edit_message_text(md2("👤 Нажмите на админа, чтобы удалить его:"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    elif data.startswith('delete_admin_'):
        admin_id_to_delete = int(data.replace('delete_admin_', ''))
        username = ADMINS.pop(admin_id_to_delete, 'Неизвестный')
        save_admins()
        await query.answer(f"Админ @{username} удален.", show_alert=True)
        lines = ["👑 Список администраторов:\n"]
        for admin_id, uname in ADMINS.items():
            name = md2(uname)
            crown = md2(" (главный)") if admin_id == ADMIN_ID else ""
            lines.append(f"\\- @{name}{crown}\n")
        text = "".join(lines)
        keyboard = [
            [InlineKeyboardButton("➖ Удалить админа", callback_data='delete_admin_list')],
            [InlineKeyboardButton("« Назад", callback_data='menu_admins')]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    elif data == 'show_stats':
        stats_text = (
            "📊 Статистика работы бота:\n"
            f"- Запросов: {bot_stats['requests']}\n"
            f"- Ошибок API: {bot_stats['errors']}"
        )
        stats_text = stats_text.replace("-", "\\-")
        keyboard = [[InlineKeyboardButton("« Назад", callback_data='open_admin_panel')]]
        await query.edit_message_text(text=stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    elif data == 'close_panel':
        await query.edit_message_text("Админ-панель закрыта.")
    elif data == 'no_op':
        await query.answer()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if context.user_data and context.user_data.get('in_conversation'):
        return
    if not BOT_IS_ACTIVE:
        return
    if not update.message.from_user.is_bot:
        chat_histories[update.message.chat.id].append(f"{update.message.from_user.first_name}: {update.message.text.strip()}")
    question = None
    if update.message.chat.type == 'private':
        question = update.message.text.strip()
    elif update.message.chat.type in ['group', 'supergroup'] and re.match(r'^Дип,', update.message.text.strip(), re.IGNORECASE):
        question = update.message.text.strip().split(',', 1)[1].strip()
    if question:
        if not CURRENT_STYLE_KEY:
            await update.message.reply_text(md2("❗ Нет ни одного стиля. Админ, добавь через /admin."), parse_mode=ParseMode.MARKDOWN_V2)
            return
        await update.message.reply_text(md2("⏳ Думаю..."), parse_mode=ParseMode.MARKDOWN_V2)
        response_text = get_model_response(question)
        await update.message.reply_text(md2(response_text), parse_mode=ParseMode.MARKDOWN_V2)

def main() -> None:
    load_data()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    style_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_style_start, pattern='^add_style_start$'),
            CallbackQueryHandler(edit_style_entry, pattern=r'^edit_style_.+')
        ],
        states={
            GET_STYLE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_name)],
            GET_STYLE_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_prompt)],
            GET_EDIT_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_edit_prompt)],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        per_user=True, per_chat=True
    )
    admin_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_admin_start, pattern='^add_admin_start$')],
        states={
            GET_ADMIN_FORWARD: [
                MessageHandler(filters.FORWARDED, get_admin_from_forward),
                MessageHandler(filters.TEXT & ~filters.COMMAND, incorrect_admin_input)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        per_user=True, per_chat=True
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel_command))
    application.add_handler(CommandHandler("fact", ai_fact_command))
    application.add_handler(admin_conv_handler)
    application.add_handler(style_conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот на движке Deepseek. Завелся, ёпт...")

    # === ДОБАВЛЕНО: запускаем веб-сервер для Render в отдельном потоке ===
    threading.Thread(target=run_web, daemon=True).start()

    application.run_polling()

# === Определения Flask-сервера (должны быть после его импортов, но до запуска потока) ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == '__main__':
    main()
