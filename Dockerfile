# =========================
# Dockerfile для Telegram-бота
# =========================

# Используем официальный Python 3.13 slim образ
FROM python:3.13-slim

# =========================
# Установка системных зависимостей для сборки пакетов с C/Rust
# =========================
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Создаем рабочую директорию
# =========================
WORKDIR /app

# =========================
# Копируем файлы проекта
# =========================
COPY requirements.txt .
COPY bot_webhook.py .
# Если есть другие нужные файлы, например .env
# COPY .env .

# =========================
# Обновляем pip и устанавливаем зависимости
# =========================
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# =========================
# Настройка переменных окружения
# =========================
ENV PORT=10000

# =========================
# Запуск бота
# =========================
CMD ["python", "bot_webhook.py"]