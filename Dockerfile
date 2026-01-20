FROM python:3.13-slim

WORKDIR /app

# Копируем только необходимые файлы
COPY bot_webhook.py /app
COPY requirements.txt /app

# Установка зависимостей
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Порт Render
ENV PORT=10000

CMD ["python", "bot_webhook.py"]
