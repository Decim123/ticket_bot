import logging
import asyncio
import openpyxl
import io
import sqlite3
import requests
from multiprocessing import Process
from aiocryptopay import AioCryptoPay, Networks
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from quart import Quart, render_template, request, jsonify, redirect, url_for, send_file
from concurrent.futures import ThreadPoolExecutor

# Задаем токены вашего бота и Crypto Pay API
TELEGRAM_TOKEN = '7156172309:AAHfsAbC2fdefm2HxCuNQ3PT2rcOE4giuuk'
CRYPTO_PAY_API_TOKEN = '256532:AAk84TvbgRtwSk3GS3ftQ6A4zvbyMSEMoTt'

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Инициализация Crypto Pay
crypto_pay = AioCryptoPay(token=CRYPTO_PAY_API_TOKEN, network=Networks.MAIN_NET)

# URL для кнопок
url = 'https://pay-cheese.fun/'

# Лексикон для сообщений
LEXICON_EN = {
    '/start': 'Бесплатный сыр только в мышеловке'
}

# Инициализация Quart-приложения
app = Quart(__name__)
executor = ThreadPoolExecutor()

def get_db_connection():
    conn = sqlite3.connect('app.db')
    conn.row_factory = sqlite3.Row
    return conn

def create_users_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            status TEXT,
            invoice_url TEXT,
            invoice_id TEXT,
            username TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_settings_db():
    conn = sqlite3.connect('setting.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            name TEXT,
            tickets_count INT,
            tickets_price FLOAT,
            date TEXT,
            password TEXT DEFAULT '123',
            winner TEXT
        )
    ''')
    
    # Проверяем, есть ли уже запись, если нет — создаем пустую запись
    cursor.execute('SELECT * FROM settings')
    if cursor.fetchone() is None:
        cursor.execute('''
            INSERT INTO settings (name, tickets_count, tickets_price, date, password, winner)
            VALUES ('', 0, 0.0, '', '123', '')
        ''')
        conn.commit()

    conn.close()

# Вызов функции для создания таблицы перед запуском приложения
create_users_table()
create_settings_db()

# Функция для получения пароля из базы данных
def get_admin_password():
    conn = sqlite3.connect('setting.db')
    cursor = conn.cursor()
    cursor.execute('SELECT password FROM settings')
    password = cursor.fetchone()[0]
    conn.close()
    return password

def price_ton():
    conn = sqlite3.connect('setting.db')
    cursor = conn.cursor()
    cursor.execute('SELECT tickets_price FROM settings')
    price = cursor.fetchone()[0]
    conn.close()
    return price

@app.route('/')
async def index():
    user_id = request.args.get('user_id')
    
    # Подключаемся к БД settings
    settings_conn = sqlite3.connect('setting.db')
    settings_cursor = settings_conn.cursor()
    
    # Извлекаем данные из таблицы settings
    settings_cursor.execute('SELECT name, tickets_count, tickets_price, date FROM settings')
    settings = settings_cursor.fetchone()
    settings_conn.close()

    settings_data = {
        'name': settings[0],
        'tickets_count': settings[1],
        'tickets_price': settings[2],
        'date': settings[3]
    }

    # Подключаемся к БД users для расчета оставшихся билетов
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE status = "suc"')
    sold_tickets = cursor.fetchone()[0]
    remaining_tickets = settings_data['tickets_count'] - sold_tickets
    print(remaining_tickets)
    conn.close()

    # Обновляем данные для отображения на странице
    settings_data['remaining_tickets'] = remaining_tickets

    if user_id:
        conn = get_db_connection()
        user = conn.execute('SELECT status, invoice_url, invoice_id FROM users WHERE tg_id = ?', (user_id,)).fetchone()

        if user is None:
            conn.execute('INSERT INTO users (tg_id, status) VALUES (?, ?)', (user_id, 'check'))
            conn.commit()
            status = 'check'
            invoice_url = None
        else:
            status = user['status']
            invoice_url = user['invoice_url']

        if invoice_url is None and status == 'check':
            invoice = await crypto_pay.create_invoice(
                asset='TON',
                amount=price_ton(),
                description="Оплата за доступ",
                payload=str(user_id),
                paid_btn_name="openBot",
                paid_btn_url='https://t.me/pay_cheese_bot'
            )
            invoice_url = invoice.bot_invoice_url
            invoice_id = invoice.invoice_id
            conn.execute('UPDATE users SET invoice_url = ?, invoice_id = ? WHERE tg_id = ?', (invoice_url, invoice_id, user_id))
            conn.commit()

        conn.close()
        return await render_template('index.html', invoice_url=invoice_url, status=status, settings=settings_data)
    
    return await render_template('index.html', settings=settings_data)

@app.route('/check_status/<int:tg_id>', methods=['GET'])
async def check_status(tg_id):
    print(f"Received tg_id for status check: {tg_id}")
    username = request.args.get('username', '')
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,)).fetchone()

    if user is None:
        print("User not found in the database. Inserting new user with 'check' status.")
        conn.execute('INSERT INTO users (tg_id, status, username) VALUES (?, ?, ?)', (tg_id, 'check', username))
        conn.commit()
        status = 'check'
        invoice_url = None
    else:
        status = user['status']
        invoice_url = user['invoice_url']
        # Проверка на наличие username и обновление только при необходимости
        if user['username'] != username:
            conn.execute('UPDATE users SET username = ? WHERE tg_id = ?', (username, tg_id))
            conn.commit()

        print(f"User status: {status}")

    if invoice_url is None and status == 'check':
        print("Creating invoice...")
        invoice = await crypto_pay.create_invoice(
            asset='TON',
            amount=price_ton(),
            description="Оплата за доступ",
            payload=str(tg_id),
            paid_btn_name="openBot",
            paid_btn_url='https://t.me/pay_cheese_bot'
        )
        print(invoice)
        invoice_url = invoice.mini_app_invoice_url
        invoice_id = invoice.invoice_id  # Получаем invoice_id
        conn.execute('UPDATE users SET invoice_url = ?, invoice_id = ? WHERE tg_id = ?', (invoice_url, invoice_id, tg_id))
        conn.commit()
        print(f"Invoice created and saved: {invoice_url}, Invoice ID: {invoice_id}")

    conn.close()
    return jsonify({'status': status, 'invoice_url': invoice_url})

@app.route('/buy_now/<int:tg_id>', methods=['POST'])
async def buy_now(tg_id):
    print(f"Received tg_id for purchase: {tg_id}")

    try:
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,)).fetchone()

        if user:
            status = user['status']
            print(f"User found in DB with status: {status}")
        else:
            print("User not found in DB, inserting new user with 'check' status.")
            conn.execute('INSERT INTO users (tg_id, status) VALUES (?, ?)', (tg_id, 'check'))
            conn.commit()
            status = 'check'

        conn.close()

        if status == 'check':
            print("Creating invoice...")
            invoice = await crypto_pay.create_invoice(
                asset='TON',
                amount=price_ton(),
                description="Оплата за доступ",
                payload=str(tg_id),
                paid_btn_name="openBot",
                paid_btn_url='https://t.me/pay_cheese_bot'
            )
            print(f"Invoice created: {invoice.web_app_invoice_url}")
            invoice_url = invoice.web_app_invoice_url
            invoice_id = invoice.invoice_id  # Получаем invoice_id
            return jsonify({'invoice_url': invoice_url, 'invoice_id': invoice_id})
        else:
            print("User already has a valid status.")
            return jsonify({'error': 'User already has a valid status'})
    
    except Exception as e:
        print(f"Error processing payment for tg_id {tg_id}: {e}")
        return jsonify({'error': f'Unable to process your request: {e}'})

@app.route('/check_payment/<int:tg_id>', methods=['POST'])
async def check_payment(tg_id):
    try:
        conn = get_db_connection()
        user = conn.execute('SELECT status, invoice_url, invoice_id FROM users WHERE tg_id = ?', (tg_id,)).fetchone()

        if user is None:
            return jsonify({'error': 'User not found'})

        invoice_url = user['invoice_url']
        invoice_id = user['invoice_id']
        if not invoice_url or not invoice_id:
            return jsonify({'error': 'No invoice URL or ID available'})

        # Логируем параметры запроса
        print(f"Checking payment status for invoice_id: {invoice_id}")

        response = await asyncio.to_thread(requests.post,
            'https://pay.crypt.bot/api/getInvoices',
            headers={'Crypto-Pay-API-Token': CRYPTO_PAY_API_TOKEN},
            json={'invoice_ids': [invoice_id]}
        )

        # Логируем ответ от API
        print(f"API response: {response.text}")
        response_data = response.json()
        
        if not response_data['ok']:
            return jsonify({'error': response_data['error']})

        items = response_data['result'].get('items', [])
        
        if len(items) > 0:
            invoice = items[0]
            if invoice['status'] == 'paid':
                conn.execute('UPDATE users SET status = ? WHERE tg_id = ?', ('suc', tg_id))
                conn.commit()
                return jsonify({'status': 'suc', 'redirect': '/'})
            else:
                return jsonify({'status': 'not_paid'})
        else:
            return jsonify({'error': 'Invoice not found'})
    
    except Exception as e:
        # Логируем все возможные ошибки
        print(f"Error during payment check: {e}")
        return jsonify({'error': str(e)})

@app.route('/admin', methods=['GET', 'POST'])
async def admin():
    admin_pass = request.args.get('pass')

    conn = sqlite3.connect('setting.db')
    cursor = conn.cursor()

    # Проверяем пароль
    cursor.execute('SELECT password FROM settings')
    stored_password = cursor.fetchone()[0]

    if admin_pass != stored_password:
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form['name']
        tickets_count = int(request.form['tickets_count'])
        tickets_price = float(request.form['tickets_price'])
        date = request.form['date']

        # Обновляем единственную запись в таблице
        cursor.execute('''
            UPDATE settings
            SET name = ?, tickets_count = ?, tickets_price = ?, date = ?
        ''', (name, tickets_count, tickets_price, date))

        conn.commit()
        conn.close()

        return redirect(url_for('admin', password=admin_pass))  # Здесь используем новое имя аргумента
    
    cursor.execute('SELECT * FROM settings')
    settings = cursor.fetchone()
    conn.close()

    return await render_template('admin.html', settings=settings)

@app.route('/download_excel', methods=['GET'])
async def download_excel():
    # Подключаемся к базе данных settings
    settings_conn = sqlite3.connect('setting.db')
    settings_cursor = settings_conn.cursor()
    settings_cursor.execute('SELECT * FROM settings')
    settings = settings_cursor.fetchone()
    settings_conn.close()

    # Подключаемся к базе данных users
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    users = cursor.fetchall()
    conn.close()

    # Создаем новый Excel файл
    workbook = openpyxl.Workbook()
    sheet = workbook.active

    # Записываем данные из settings
    sheet['A1'] = 'Название'
    sheet['B1'] = 'Количество билетов'
    sheet['C1'] = 'Цена билета'
    sheet['D1'] = 'Дата'
    sheet['E1'] = 'Пароль'
    sheet['F1'] = 'Победитель'

    sheet.append(list(settings))  # Преобразуем кортеж settings в список

    # Пустая строка перед таблицей users
    sheet.append([])

    # Записываем заголовки для таблицы users
    sheet.append(['tg_id', 'status', 'invoice_url', 'invoice_id', 'username'])

    # Записываем данные из users
    for user in users:
        sheet.append(list(user))  # Преобразуем каждую строку из users в список

    # Сохраняем файл в памяти
    file_stream = io.BytesIO()
    workbook.save(file_stream)
    file_stream.seek(0)

    # Генерируем имя файла на основе name из settings
    filename = f"{settings[0]}.xlsx"

    # Отправляем файл пользователю
    return await send_file(
        file_stream,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/clear_users_db', methods=['GET'])
async def clear_users_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users')  # Удаляем все записи из таблицы users
    conn.commit()
    conn.close()
    return redirect(url_for('admin', password=request.args.get('pass')))  # Возвращаемся на страницу администрирования


# Обработка команды /start для Telegram бота
@router.message(CommandStart())
async def process_start_command(message: Message):
    path_to_photo = "app/static/images/start.jpg"

    # Создание клавиатуры с кнопками
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Открыть", web_app=types.WebAppInfo(url=url)),
            InlineKeyboardButton(text="Подписаться", url="https://t.me/pay_cheese")
        ]
    ])

    # Отправка сообщения с фото и кнопками
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=types.FSInputFile(path_to_photo),
        caption=LEXICON_EN['/start'],
        reply_markup=inline_kb
    )

# Хендлер для обработки сообщений
@router.message()
async def handle_password_message(message: Message):
    user_message = message.text.strip()
    admin_password = get_admin_password()

    if user_message == admin_password:
        # Создаем клавиатуру с кнопкой
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Админ панель", url=f"{url}/admin?pass={admin_password}")
            ]
        ])
        await message.answer("Админ панель", reply_markup=inline_kb)
    else:
        # Игнорируем сообщение, если оно не совпадает с паролем
        return

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await dp.start_polling(bot)

def start_quart():
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == '__main__':
    quart_process = Process(target=start_quart)
    quart_process.start()
    asyncio.run(main())
    quart_process.join()
