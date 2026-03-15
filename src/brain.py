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

        # Подключаемся к базе
        self.db_client = chromadb.PersistentClient(path=db_path)
        # Если базы еще нет (или она пустая), мы ее создадим позже
        self.collection = self.db_client.get_or_create_collection("knowledge")

    async def get_embedding(self, text):
        """Получаем вектор через OpenAI"""
        text = text.replace("\n", " ")
        response = await self.client_ai.embeddings.create(
            input=[text],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    async def get_answer(self, user_question: str):
        # 1. Генерируем вектор вопроса через API
        query_vector = await self.get_embedding(user_question)

        # 2. Ищем в базе
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=3
        )

        context = " ".join(results['documents'][0]) if results['documents'] else ""

        # 3. Формируем ответ
        system_prompt = f"""Ты — ИИ-ассистент. Отвечай кратко и профессионально. 
        Используй контекст: {context}
        Если ответа нет, предложи оставить контакты."""

        response = await self.client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_question}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content