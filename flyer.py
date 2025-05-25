import requests
import re
from PyPDF2 import PdfMerger
import os
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

max_retries = 10
retry_delay = 1

import requests
import sys
from io import StringIO


class WebhookIO(StringIO):
    def __init__(self, original_stream, webhook_url, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_stream = original_stream
        self.webhook_url = webhook_url
        self.buffer = ""

    def write(self, s):
        # Перенаправляем в оригинальный поток
        self.original_stream.write(s)

        # Добавляем в буфер
        self.buffer += s

        # Если есть перевод строки, отправляем сообщение
        if '\n' in self.buffer:
            self.flush()

    def flush(self):
        if self.buffer.strip():
            # Форматируем и отправляем через вебхук
            message = f"```{self.buffer.strip()}```"[:1990]
            data = {
                'content': message,
                'avatar_url': "#",
                'username': "Флаер"
            }
            response = requests.post(self.webhook_url, json=data)
            if response.status_code != 204:
                self.original_stream.write(f"Webhook error: {response.text}\n")

        # Очищаем буфер
        self.buffer = ""
        super().flush()


def setup_webhook_logging(webhook_url):
    # Перенаправляем stdout и stderr
    sys.stdout = WebhookIO(sys.stdout, webhook_url)
    sys.stderr = WebhookIO(sys.stderr, webhook_url)


# Пример использования
WEBHOOK_URL = "#"
setup_webhook_logging(WEBHOOK_URL)

async def aip_find_links(airport, update: Update):
    aip_url = "http://www.caica.ru/ANI_Official/Aip/html/menurus.htm"
    aip_filt_links = []

    try:
        response = requests.get(aip_url)
        response.raise_for_status()
        html_content = response.text

        pattern = rf'ItemLink\("([^"]*\/{airport.lower()}\/[^"]*)","([^"]*)"'
        matches = re.findall(pattern, html_content)

        # Формируем сообщение со ссылками
        links_message = f"Найдено {len(matches)} PDF-файлов для аэропорта {airport.upper()}:\n"
        print(links_message)
        too_long = False

        for link, name in matches:
            full_url = f"http://www.caica.ru/ANI_Official/Aip{link[2:]}"
            combined = f"{full_url} & {name}"
            aip_filt_links.append(combined)
            links_message += f"[{name}]({full_url})\n"
            if len(links_message) > 1800:
                await update.message.reply_markdown(links_message)
                links_message = ""
                too_long = True
        if not too_long:
            await update.message.reply_markdown(links_message)

        # Отправляем сообщение пользователю со всеми ссылками
        await update.message.reply_text("Начинаю скачивание и объединение файлов... Это может занять некоторое время")

    except requests.exceptions.RequestException as e:
        print(e)
        await update.message.reply_text(f"Ошибка при запросе к AIP: {e}")
        exit(1)

    return aip_filt_links


def aip_download_with_retries(url, name, max_attempts, temp_filename):
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()

            with open(temp_filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)

            return True

        except Exception as e:
            print(e)
            if attempt < max_attempts:
                time.sleep(retry_delay)

    return False


async def aip_download_and_merge_pdfs(url_list, output_filename, icao, update: Update):
    # Создаем папку для временных файлов с user_id в названии
    user_id = update.message.from_user.id
    temp_dir = f"temp_{icao.lower()}_{user_id}"
    os.makedirs(temp_dir, exist_ok=True)

    merger = PdfMerger()
    temp_files = []
    failed_downloads = []

    for i, combined in enumerate(url_list):
        url, name = combined.split(" & ", 1)
        temp_filename = os.path.join(temp_dir, f"temp_{i}.pdf")

        if aip_download_with_retries(url, name, max_retries, temp_filename):
            try:
                merger.append(temp_filename)
                temp_files.append(temp_filename)
            except Exception as e:
                print(e)
                failed_downloads.append(name)
        else:
            failed_downloads.append(name)

    if merger.pages:
        merger.write(output_filename)
        await update.message.reply_text(f"Успешно объединено {len(url_list) - len(failed_downloads)}/{len(url_list)} файлов")

    merger.close()

    # Удаляем временные файлы и папку
    for temp_file in temp_files:
        try:
            os.remove(temp_file)
        except:
            pass

    try:
        os.rmdir(temp_dir)
    except:
        pass

    if failed_downloads:
        print('\n'.join(failed_downloads))
        await update.message.reply_text(f"Не удалось скачать {len(failed_downloads)} файлов")


async def aip(airport, update: Update):
    aip_filt_links = await aip_find_links(airport, update)

    if aip_filt_links:
        output_file = f"{airport.upper()}.pdf"
        await aip_download_and_merge_pdfs(aip_filt_links, output_file, airport, update)
        return output_file
    else:
        print(f"Не найдено для {airport}")
        await update.message.reply_text("Не найдено PDF-файлов для указанного аэропорта.")
        return None


async def aip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите ICAO код аэропорта. Например: /aip UHHH")
        return

    icao = context.args[0].upper()

    try:
        file_path = await aip(icao, update)
        if file_path:
            # Отправляем файл
            with open(file_path, 'rb') as file:
                await update.message.reply_document(
                    document=file,
                    filename=f"{icao}.pdf",
                    caption=f"AIP {icao}"
                )

            # Удаляем временный файл
            try:
                os.remove(file_path)
            except Exception as e:
                print(e)
                pass
        else:
            print(f"Не найдено для {icao}")
            await update.message.reply_text(f"Не найдено PDF-файлов для аэропорта {icao}.")

    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка: {str(e)}")
        print(f"Произошла ошибка: {str(e)}")


def main() -> None:
    # Получаем токен из переменных окружения
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        print('token not found')
        raise ValueError("Токен бота не найден в .env файле")

    # Создаем Application
    application = Application.builder().token(token).read_timeout(300).write_timeout(300).build()

    # Добавляем обработчик команды /aip
    application.add_handler(CommandHandler("aip", aip_command))

    # Запускаем бота
    application.run_polling()


if __name__ == "__main__":
    main()
