import os
import json
import logging
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters.command import Command
from aiogram.types import FSInputFile
import io

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния FSM
class FileProcess(StatesGroup):
    waiting_for_file = State()
    processing = State()

# Команда старт
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Привет! Я бот для обработки файлов с тестовыми вопросами.\n"
        "Пожалуйста, отправьте мне файл в формате DOCX, PDF или XLSX с вопросами и ответами.\n"
        "Я обработаю его и верну вам JSON с структурированными данными."
    )
    await state.set_state(FileProcess.waiting_for_file)

# Обработчик документов
@dp.message(F.document, FileProcess.waiting_for_file)
async def process_document(message: types.Message, state: FSMContext):
    # Установка состояния обработки
    await state.set_state(FileProcess.processing)
    
    # Информирование пользователя
    processing_message = await message.answer("Получил файл. Обрабатываю...")
    
    try:
        # Получение информации о файле
        document = message.document
        file_name = document.file_name
        file_extension = file_name.split('.')[-1].lower() if '.' in file_name else None
        
        # Проверка расширения файла
        if file_extension not in ['docx', 'pdf', 'xlsx']:
            await message.answer("Пожалуйста, отправьте файл формата DOCX, PDF или XLSX.")
            await state.set_state(FileProcess.waiting_for_file)
            return
        
        # Скачивание файла
        file_id = document.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_content = await bot.download_file(file_path)
        
        # Обработка файла через OpenAI API
        json_data = await process_file_with_openai(file_content, file_name, file_extension)
        
        # Создание файла JSON для отправки
        json_filename = f"{file_name.split('.')[0]}_processed.json"
        with open(json_filename, 'w', encoding='utf-8') as json_file:
            json_file.write(json_data)
        
        # Отправка файла пользователю
        await message.answer_document(
            FSInputFile(json_filename),
            caption="Вот результат обработки файла."
        )
        
        # Удаление временного файла
        os.remove(json_filename)
        
        # Сброс состояния
        await state.set_state(FileProcess.waiting_for_file)
        
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}")
        await message.answer(f"Произошла ошибка при обработке файла: {str(e)}")
        await state.set_state(FileProcess.waiting_for_file)
    finally:
        # Удаление сообщения о обработке
        await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)

async def process_file_with_openai(file_content, file_name, file_extension):
    """Напрямую отправляет файл в OpenAI API и обрабатывает его содержимое."""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Создаем временный файл для отправки
    temp_file_path = f"temp_{file_name}"
    with open(temp_file_path, 'wb') as temp_file:
        temp_file.write(file_content.read())
    
    try:
        async with aiohttp.ClientSession() as session:
            # Загрузка файла в OpenAI
            file_upload_headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}"
            }
            
            form_data = aiohttp.FormData()
            form_data.add_field(
                'purpose',
                'assistants'
            )
            form_data.add_field(
                'file',
                open(temp_file_path, 'rb'),
                filename=file_name,
                content_type=f'application/{file_extension}'
            )
            
            async with session.post(
                "https://api.openai.com/v1/files",
                headers=file_upload_headers,
                data=form_data
            ) as upload_response:
                if upload_response.status != 200 and upload_response.status != 201:
                    error_text = await upload_response.text()
                    raise Exception(f"OpenAI API вернул ошибку при загрузке файла: {upload_response.status} - {error_text}")
                
                upload_result = await upload_response.json()
                file_id = upload_result['id']
                
                # Используем загруженный файл в запросе
                chat_data = {
                    "model": "gpt-4-turbo",
                    "messages": [
                        {
                            "role": "system",
                            "content": """
                            Проанализируй файл с тестовыми вопросами и ответами. 
                            Извлеки все вопросы, варианты ответов и укажи правильный ответ.
                            Верни данные в формате JSON следующей структуры:
                            [
                                {
                                    "question": "Текст вопроса",
                                    "answers": ["Вариант 1", "Вариант 2", "Вариант 3", ...],
                                    "correct_answer": "Правильный ответ"
                                },
                                ...
                            ]
                            """
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Проанализируй этот файл ({file_name}) и извлеки все тестовые вопросы и ответы в JSON формате, как указано."
                                },
                                {
                                    "type": "file_path",
                                    "file_path": {"file_id": file_id}
                                }
                            ]
                        }
                    ]
                }
                
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=chat_data
                ) as chat_response:
                    if chat_response.status != 200:
                        error_text = await chat_response.text()
                        raise Exception(f"OpenAI API вернул ошибку при обработке: {chat_response.status} - {error_text}")
                    
                    chat_result = await chat_response.json()
                    
                    # Удаление загруженного файла из OpenAI
                    async with session.delete(
                        f"https://api.openai.com/v1/files/{file_id}",
                        headers=file_upload_headers
                    ) as delete_response:
                        if delete_response.status != 200 and delete_response.status != 204:
                            logger.warning(f"Не удалось удалить файл из OpenAI: {file_id}")
                    
                    return chat_result['choices'][0]['message']['content']
    
    except Exception as e:
        logger.error(f"Ошибка при обработке через OpenAI API: {e}")
        raise e
    
    finally:
        # Удаляем временный файл
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

# Обработчик для неизвестных команд или сообщений
@dp.message()
async def process_other_messages(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == FileProcess.waiting_for_file:
        await message.answer("Пожалуйста, отправьте файл в формате DOCX, PDF или XLSX.")
    else:
        await message.answer("Я не понимаю эту команду. Отправьте /start, чтобы начать работу.")

async def main():
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())