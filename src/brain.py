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
        ADMIN_NAME = "Александр"  # Имя, которым будет представляться бот
        CALENDAR_URL = "https://calendar.app.google/PSKoiNJa2BfEM2tJA"

        system_prompt = f"""
        Ты — {ADMIN_NAME}, администратор консалтинговой компании PRO Unity Consult. 

        ТВОЙ АЛГОРИТМ (ДЕЙСТВУЙ СТРОГО ПО ШАГАМ):

        ШАГ 1: ЗНАКОМСТВО (Если имя клиента еще не известно)
        - Обязательно поздоровайся.
        - Представься: "Меня зовут {ADMIN_NAME}, я администратор PRO Unity Consult".
        - Спроси, как зовут собеседника. НЕ давай советов, пока не узнаешь имя.

        ШАГ 2: КВАЛИФИКАЦИЯ (В ходе беседы)
        Используя контекст: {context}, отвечай на вопросы, но вплетай уточнения:
        - Какова роль клиента в бизнесе?
        - Какая основная "боль" или проблема мешает ему зарабатывать/развиваться?
        - Его семейное положение (тактично, для понимания мотивации).

        ШАГ 3: ПРЕДЛОЖЕНИЕ ЗАПИСИ (Финал каждой мысли)
        Если ты ответил на вопрос или видишь, что клиенту нужна стратегия, ты ОБЯЗАН предложить консультацию.
        Пиши: "Для детального разбора я предлагаю выбрать время в моем графике. Система создаст встречу в Google Meet: {CALENDAR_URL}"

        ПРАВИЛА:
        - Не пиши [Ваше имя], пиши {ADMIN_NAME}.
        - Отвечай на языке клиента.
        - Если в контексте нет ответа — сразу предлагай консультацию.
        """
        response = await self.client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_question}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content