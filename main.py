import requests
import sqlite3
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Заменить на токен телеграмм-бота
TELEGRAM_BOT_TOKEN = 'YOUR_TOKEN_HERE'
# URL для API Wildberries
WILDBERRIES_BASE_URL = 'https://supplies-api.wildberries.ru/api/v1'

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        api_token TEXT
    )
    ''')
    conn.commit()
    conn.close()

def get_api_token(telegram_id):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT api_token FROM users WHERE telegram_id = ?', (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def set_api_token(telegram_id, api_token):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('REPLACE INTO users (telegram_id, api_token) VALUES (?, ?)', (telegram_id, api_token))
    conn.commit()
    conn.close()

# Словарь для хранения данных пользователей
user_data = {}

# Словарь соответствий ID складов и их названий
WAREHOUSE_NAMES = {
    1733: 'Екатеринбург',
    507: 'Коледино',
    124731: 'Крёкшино КБТ',
    686: 'Новосибирск',
    2737: 'Санкт-Петербург',
    117986: 'Казань',
    130744: 'Краснодар',
    117501: 'Подольск',
    204939: 'Астана',
    159402: 'Санкт-Петербург Шушары',
    205228: 'Белая Дача',
    120762: 'Электросталь',
    121709: 'Электросталь КБТ',
    206348: 'Тула',
    206968: 'Чехов',
    208941: 'Домодедово',
    208277: 'Невинномысск',
    210001: 'Чехов 2',
    210515: 'Вёшки',
    211622: 'Минск',
    1193: 'Хабаровск',
    207743: 'Пушкино',
    218210: 'Обухово',
    218623: 'Подольск 3',
    218987: 'Алматы Атакент',
    300571: 'Екатеринбург 2',
    300168: 'Радумля',
    205985: 'Крыловская',
    301229: 'Подольск 4',
    300711: 'Уральск',
    301920: 'Пятигорск',
    301760: 'Рязань (Тюшевское)',
    206236: 'Белые столбы',
    302335: 'Кузнецк',
    303295: 'Клин',
    312807: 'Обухово 2',
    218644: 'Хабаровск 2',
    301809: 'Котовск',
    301805: 'Новосемейкино',
    312259: 'СЦ Шушары',
    301983: 'СЦ Волгоград',
    218628: 'СЦ Ижевск',
    302737: 'СЦ Барнаул',
    316879: 'СЦ Актобе',
    321932: 'Чашниково',
    324108: 'Астана 2'
}

# Создание глобальных переменных для управления задачами
monitoring_jobs = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.message.from_user.id
    keyboard = [
        [InlineKeyboardButton(get_api_token_button_text(telegram_id), callback_data='setapi')],
        [InlineKeyboardButton("Начать мониторинг", callback_data='monitor')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Привет! Что вы хотите сделать?', reply_markup=reply_markup)

def get_api_token_button_text(telegram_id):
    """Получить текст кнопки 'Ввести API токен' с учетом состояния."""
    api_token = get_api_token(telegram_id)
    if api_token:
        return "Ввести API токен [✅]"
    else:
        return "Ввести API токен"

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # Проверяем состояние и показываем соответствующие кнопки
    if query.data == 'monitor':
        await query.edit_message_text(
            text="Введите идентификаторы складов через запятую.\nИдентификаторы можно получить здесь⬇️\nhttps://ibb.co/GWB44ZQ",
            reply_markup=back_button()
        )
        context.user_data['awaiting'] = 'monitor'
    elif query.data == 'setapi':
        await query.edit_message_text(
            text="Введите ваш API токен (или нажмите 'Назад' для возврата):",
            reply_markup=back_button()
        )
        context.user_data['awaiting'] = 'setapi'
    elif query.data == 'back':
        # Возвращаемся к начальному экрану
        telegram_id = query.from_user.id
        await query.edit_message_text(
            text="Привет! Что вы хотите сделать?",
            reply_markup=start_button(telegram_id)
        )
        # Удаляем состояние "awaiting", чтобы избежать путаницы
        context.user_data.pop('awaiting', None)
    elif query.data == 'stop_monitor':
        # Остановка мониторинга
        user_id = query.from_user.id
        if user_id in monitoring_jobs:
            monitoring_jobs[user_id]['job'].schedule_removal()
            del monitoring_jobs[user_id]
            await query.edit_message_text(
                text="Мониторинг остановлен.",
                reply_markup=start_button(user_id)
            )
        else:
            await query.edit_message_text(
                text="Мониторинг не запущен.",
                reply_markup=start_button(user_id)
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    text = update.message.text

    if 'awaiting' not in context.user_data:
        await update.message.reply_text('Сначала выберите действие через /start.')
        return

    action = context.user_data.pop('awaiting')

    if action == 'monitor':
        api_token = get_api_token(user_id)
        if not api_token:
            await update.message.reply_text('Сначала введите API токен с помощью команды /setapi.')
            return

        warehouse_ids = list(map(int, text.split(',')))
        context.user_data['warehouse_ids'] = warehouse_ids

        if user_id in monitoring_jobs:
            monitoring_jobs[user_id]['job'].schedule_removal()
            del monitoring_jobs[user_id]

        job_queue = context.job_queue
        job = job_queue.run_repeating(
            lambda job_context: check_warehouses(job_context, user_id),
            interval=5,
            first=0
        )

        monitoring_jobs[user_id] = {
            'job': job,
            'warehouse_ids': warehouse_ids
        }

        await update.message.reply_text(
            'Мониторинг начат! Для остановки нажмите кнопку "Остановить мониторинг".',
            reply_markup=stop_button()
        )
    elif action == 'setapi':
        api_token = text
        set_api_token(user_id, api_token)
        await update.message.reply_text('API токен сохранен!')
        # Обновляем кнопки в текущем чате
        await update.message.reply_text(
            'Привет! Что вы хотите сделать?',
            reply_markup=start_button(user_id)
        )

async def check_warehouses(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    warehouse_ids = monitoring_jobs[user_id]['warehouse_ids']
    api_token = get_api_token(user_id)

    if api_token and warehouse_ids:
        coefficients = get_acceptance_coefficients(api_token, warehouse_ids)
        if coefficients:
            for item in coefficients:
                warehouse_id = item['warehouseID']
                coefficient = item.get('coefficient', None)
                box_type_id = item.get('boxTypeID', None)

                if coefficient == 0 and box_type_id == 2: #коэффицент приемки и тип приёмки
                    warehouse_name = WAREHOUSE_NAMES.get(warehouse_id, f'Склад {warehouse_id}')
                    await context.bot.send_message(chat_id=user_id, text=f'Склад {warehouse_name}: Бесплатно!')
        else:
            pass

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back')]])

def start_button(telegram_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_api_token_button_text(telegram_id), callback_data='setapi')],
        [InlineKeyboardButton("Начать мониторинг", callback_data='monitor')]
    ])

def stop_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Остановить мониторинг", callback_data='stop_monitor')]])

def get_acceptance_coefficients(api_token, warehouse_ids):
    headers = {
        'Authorization': f'Bearer {api_token}'
    }
    params = {
        'warehouseIDs': ','.join(map(str, warehouse_ids))
    }
    response = requests.get(f'{WILDBERRIES_BASE_URL}/acceptance/coefficients', headers=headers, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        return None

def main():
    init_db()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    job_queue = application.job_queue

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == '__main__':
    main()
