# -------------------------------
# Dockerfile для Telegram бота
# -------------------------------

# Базовый образ с Python 3.11
FROM python:3.11-slim

# Устанавливаем системные зависимости для сборки
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libffi-dev \
        gcc \
        g++ \
        wget \
        unzip \
        && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY . /app

# Обновляем pip и ставим зависимости
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

# Указываем переменные окружения для Render
ENV PORT=10000

# Указываем команду запуска
CMD ["python", "bot_webhook.py"]
