# =========================
# Используем Python 3.13 slim
FROM python:3.13-slim

# =========================
# Обновляем pip и устанавливаем зависимости системы
RUN apt-get update && apt-get install -y build-essential curl && \
    python -m pip install --upgrade pip

# =========================
# Устанавливаем Python-зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# =========================
# Копируем проект
WORKDIR /app
COPY . /app

# =========================
# Порт Render
ENV PORT=10000
EXPOSE 10000

# =========================
# Запуск бота через webhook
CMD ["python", "bot_webhook_1.py"]
