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

    async def get_answer(self, user_question: str, chat_history: list = None):
        """
        Генерирует ответ, учитывая знания из базы и историю переписки.
        chat_history: список словарей [{'role': 'user', 'content': '...'}, ...]
        """
        # 1. Поиск релевантных знаний в ChromaDB
        query_vector = await self.get_embedding(user_question)
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=3
        )
        context = " ".join(results['documents'][0]) if results['documents'] else ""

        # 2. Настройки личности и инструментов
        ADMIN_NAME = "Александр"
        CALENDAR_URL = "https://calendar.app.google/PSKoiNJa2BfEM2tJA"

        # Формируем системную инструкцию
        system_prompt = f"""Ты — {ADMIN_NAME}, администратор консалтинговой компании PRO Unity Consult. 

ТВОЙ АЛГОРИТМ (ДЕЙСТВУЙ СТРОГО ПО ШАГАМ):

ШАГ 1: ЗНАКОМСТВО
- Если в истории переписки ты еще не знаешь имени клиента, поздоровайся, представься как {ADMIN_NAME} и ОБЯЗАТЕЛЬНО спроси, как зовут собеседника.
- Не давай развернутых советов, пока не узнаешь имя.

ШАГ 2: КВАЛИФИКАЦИЯ (Используй контекст: {context})
- В ходе беседы тактично узнай: роль клиента в бизнесе, его основную "боль" и семейное положение.
- Отвечай по существу, используя предоставленный контекст.

ШАГ 3: ПРЕДЛОЖЕНИЕ ЗАПИСИ
- В конце каждого содержательного ответа предлагай записаться на консультацию для детального разбора.
- Ссылка для записи: {CALENDAR_URL}

ПРАВИЛА:
- Говори на языке клиента, будь вежлив и профессионален.
- Если в контексте нет ответа — предложи обсудить это лично на консультации."""

        # 3. Формируем пакет сообщений для OpenAI
        messages = [{"role": "system", "content": system_prompt}]

        # Если есть история из БД, добавляем её (последние 10 сообщений для памяти)
        if chat_history:
            messages.extend(chat_history[-10:])

        # Добавляем текущий вопрос пользователя
        messages.append({"role": "user", "content": user_question})

        # 4. Запрос к GPT
        response = await self.client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3
        )

        return response.choices[0].message.content