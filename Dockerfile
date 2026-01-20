FROM python:3.13-slim

# Установка зависимостей ОС
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Работаем в директории проекта
WORKDIR /app
COPY requirements.txt .

# Устанавливаем Python-зависимости (только бинарные колёса)
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# Копируем проект
COPY . /app

# Порт Render
ENV PORT=10000
EXPOSE 10000

# Запуск бота
CMD ["python", "bot_webhook_1.py"]
