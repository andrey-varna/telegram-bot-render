# Используем Python 3.11
FROM python:3.11-slim

# Устанавливаем системные зависимости (нужны для сборки некоторых библиотек, например, хромы)
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Создаём рабочую директорию
WORKDIR /app

# Сначала копируем только requirements для кэширования слоев
COPY requirements.txt .
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект (включая папку src, data и main.py)
COPY . .

# Порт Render
ENV PORT=10000
EXPOSE 10000

# Запуск
CMD ["python", "bot_webhook.py"]
