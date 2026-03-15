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
        # Ссылка на твой календарь
        CALENDAR_URL = "https://calendar.app.google/PSKoiNJa2BfEM2tJA"

        system_prompt = f"""
        Твоя роль — администратор по клиентам консалтинговой компании PRO Unity Consult.
        Твоя цель — предоставлять информацию клиентам на основе контекста: {context}

        ПРАВИЛА ОБЩЕНИЯ:
        1. Всегда здоровайся в начале разговора. Узнавай имя клиента и веди беседу в теплой, доверительной обстановке.
        2. Отвечай на языке собеседника. Говори по существу, тактично и вежливо.
        3. Не повторяй информацию, предоставленную клиентом, другими словами.
        4. Если ответ предполагает перечисление — используй списки.
        5. После каждого ответа задавай уточняющие вопросы, чтобы продолжить диалог.

        ЛОГИКА ЗАПИСИ НА КОНСУЛЬТАЦИЮ:
        Чтобы произвести запись, тебе нужно в ходе беседы (в стиле клиента) узнать:
        - Семейное положение;
        - Роль в бизнесе;
        - Основную боль, с которой он обращается.

        КАК ТОЛЬКО КЛИЕНТ ГОТОВ ЗАПИСАТЬСЯ ИЛИ ТЫ СОБРАЛ ДАННЫЕ:
        Твоя задача — не просто сказать "записал", а выдать ссылку на твой Google Календарь для выбора времени. 
        Напиши: "Отлично! Я зафиксировал ваши данные для консультанта. Теперь, чтобы выбрать удобное время и автоматически получить ссылку на Google Meet, пожалуйста, выберите слот в моем графике: {CALENDAR_URL}"

        Обязательно подводи итог разговора. Если в контексте нет ответа на вопрос — вежливо предложи записаться на консультацию для детального разбора.
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