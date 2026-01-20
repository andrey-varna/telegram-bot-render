# Используем проверенный рабочий образ
FROM python:3.11-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создаём рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY requirements.txt .
COPY bot_webhook.py .

# Обновляем pip и устанавливаем зависимости
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Порт Render
ENV PORT=10000
EXPOSE 10000

# Запуск
CMD ["python", "bot_webhook.py"]
