import os
import chromadb
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()


class AssistantBrain:
    def __init__(self):
        # Определяем пути
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base_dir, "data")

        self.client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Подключаемся к векторной базе ChromaDB
        self.db_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.db_client.get_or_create_collection("knowledge")

    async def get_embedding(self, text):
        """Получаем вектор через OpenAI"""
        text = text.replace("\n", " ")
        response = await self.client_ai.embeddings.create(
            input=[text],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    async def get_answer(self, user_question: str, chat_history: list = None, user_name: str = None):
        """
        Генерирует ответ, учитывая знания из базы и историю переписки.
        """
        if chat_history is None:
            chat_history = []

        # --- 1. ОГРАНИЧЕНИЕ КОЛИЧЕСТВА ВОПРОСОВ ---
        # Считаем сообщения пользователя в истории
        user_questions_count = sum(1 for msg in chat_history if msg.get("role") == "user")

        if user_questions_count >= 5:
            return (
                "Вижу, у вас много глубоких вопросов! Чтобы разобрать вашу ситуацию "
                "максимально точно, я приглашаю вас на диагностику. Там мы разберем всё "
                "профессионально. Записаться можно здесь: https://calendar.app.google/PSKoiNJa2BfEM2tJA"
            )

        # --- 2. ПОИСК РЕЛЕВАНТНЫХ ЗНАНИЙ (ChromaDB) ---
        query_vector = await self.get_embedding(user_question)
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=3
        )
        context = " ".join(results['documents'][0]) if results['documents'] else ""

        # --- 3. НАСТРОЙКИ ЛИЧНОСТИ И ПРАВИЛА ---
        ADMIN_NAME = "Александр"
        CALENDAR_URL = "https://calendar.app.google/PSKoiNJa2BfEM2tJA"

        # Если имя передано, мы говорим Александру, что он уже знает клиента
        intro_instruction = ""
        if user_name and user_name != "Гость":
            intro_instruction = f"Ты УЖЕ знаком с клиентом, его зовут {user_name}. Обращайся к нему по имени и не спрашивай его снова."
        else:
            intro_instruction = "Если ты еще не знаешь имени клиента, представься и спроси, как его зовут."

        system_prompt = f"""Ты — {ADMIN_NAME}, администратор консалтинговой компании PRO Unity Consult. 

    ТВОЙ АЛГОРИТМ (ДЕЙСТВУЙ СТРОГО ПО ШАГАМ):

    ШАГ 1: ЗНАКОМСТВО
    - Если в истории переписки ты еще не знаешь имени клиента, поздоровайся, представься как {ADMIN_NAME} и ОБЯЗАТЕЛЬНО спроси, как зовут собеседника.
    - Не давай развернутых советов, пока не узнаешь имя.

    ШАГ 2: КВАЛИФИКАЦИЯ (Используй контекст: {context})
    - В ходе беседы тактично узнай: роль клиента в бизнесе, его основную "боль" и семейное положение.
    - Отвечай по существу, используя ПРЕДОСТАВЛЕННЫЙ КОНТЕКСТ.
    - ВАЖНО: Если в контексте нет информации об услуге или цене — НЕ ПРИДУМЫВАЙ. Скажи: 'У меня нет точных данных в базе по этому вопросу, но эксперт расскажет об этом на диагностике'.

    ШАГ 3: ПРЕДЛОЖЕНИЕ ЗАПИСИ
    - В конце каждого содержательного ответа предлагай записаться на консультацию для детального разбора.
    - Ссылка для записи: {CALENDAR_URL}

    ПРАВИЛА:
    - Говори на языке клиента, будь вежлив и профессионален.
    - Используй ТОЛЬКО простой текст. Запрещено использовать разметку Markdown (квадратные или круглые скобки для ссылок). 
    - Пиши ссылку {CALENDAR_URL} просто текстом, чтобы она была видна и кликабельна везде.
    """

        # --- 4. ФОРМИРОВАНИЕ ПАКЕТА СООБЩЕНИЙ ---
        messages = [{"role": "system", "content": system_prompt}]

        # Добавляем последние 10 сообщений для памяти
        if chat_history:
            messages.extend(chat_history[-10:])

        # Добавляем текущий вопрос пользователя
        messages.append({"role": "user", "content": user_question})

        # --- 5. ЗАПРОС К GPT ---
        try:
            response = await self.client_ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.3  # Низкая температура снижает риск выдумок
            )
            return response.choices[0].message.content
        except Exception as e:
            import logging
            logging.error(f"OpenAI Error: {e}")
            return f"Извините, сейчас я не могу ответить. Пожалуйста, запишитесь на диагностику: {CALENDAR_URL}"