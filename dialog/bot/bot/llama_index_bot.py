import json
import re
from pathlib import Path
from typing import List

import llama_index.core
import pydantic
import requests
from llama_index.core import Settings, StorageContext, load_index_from_storage, Document, GPTListIndex, VectorStoreIndex
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.indices.base import BaseIndex
from llama_index.core.indices.list.base import ListRetrieverMode
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.text_generation_inference import TextGenerationInference
from typing_extensions import Optional, override

from .base import BotRequestContext, PropertyKey as K, BotResponse
from .qa_resource_analyzer import (QaResourceAnalyzer, QaResourceStringQuery, QaResourceQuery, QaResourceDatetimeQuery,
                                   SIMPLE_DT_FORMAT)
from .rag_bot_base import LTRagBotBase


class AppConfig(pydantic.BaseModel):
    system_prompt: str
    context_template: str
    start_message: str
    content_directory: Optional[str] = None


llama_index.core.set_global_handler("simple")


class LlamaIndexBot(LTRagBotBase):

    def __init__(self,
                 fallback_vectorstore_dir: str,
                 user_chat_history_dir: str,
                 default_app_name='lectureAssistant'
                 ):
        super().__init__(fallback_vectorstore_dir, user_chat_history_dir)
        self._default_app_name = default_app_name

        Settings.llm = TextGenerationInference(
            model_name="llama3.1",
            temperature=0.0,
            max_tokens=512,
            model_url="http://192.168.0.66:8054",
        )
        Settings.embed_model = HuggingFaceEmbedding(
            model_name="BAAI/bge-base-en",
            cache_folder="./hf-model-cache/"
        )
        Settings.chunk_size = 256
        Settings.chunk_overlap = 50

        self._qa_resource_analyzer = QaResourceAnalyzer(Settings.llm)
        self._llm = Settings.llm

        apps_file = Path(__file__).parent.parent / 'apps.json'
        self._apps = {name: AppConfig.model_validate(cfg)
                      for name, cfg in json.loads(apps_file.read_text()).items()}

        # Assume default app is online session (context is dynamic).
        self._selected_app: K[AppConfig] = K('selected_app', AppConfig, scope='session',
                                             default=self._apps[self._default_app_name])
        self._chat_history: K[List[ChatMessage]] = K('chat_history', List[ChatMessage], default=list)

    def answer_chat_message(self, user_message: str, context: BotRequestContext, additional_data: dict) -> BotResponse:
        idx_dirs = self.get_vector_idx_dir(context)

        # Load and combine all found vector stores into one (if there are more than one).
        vector_store_indexes: list = []
        for idx_dir in idx_dirs:
            storage_context = StorageContext.from_defaults(persist_dir=str(idx_dir))
            vector_store_index = load_index_from_storage(storage_context)
            vector_store_indexes.append(vector_store_index)
        if len(vector_store_indexes) == 1:
            vector_store_index = vector_store_indexes[0]
        else:
            vector_store_index = VectorStoreIndex.from_documents([])
            for vsi in vector_store_indexes:
                vector_store_index.insert_nodes(list(vsi.docstore.docs.values()))

        if self._qa_resource_analyzer:
            queries = self._qa_resource_analyzer.analyze(user_message)
        else:
            queries = [QaResourceStringQuery('content', user_message)]

        context_messages, used_context = self._perform_resource_queries(queries, vector_store_index,
                                                                        context, additional_data)
        answer = self._answer_question(user_message, context_messages, context)
        print("context used: ", used_context)
        print("full response", answer)
        return BotResponse(answer, used_context)

    def _perform_resource_queries(self, resource_queries: List[QaResourceQuery],
                                  content_vs: BaseIndex, context: BotRequestContext,
                                  extra_data: dict):
        content_query_engine = content_vs.as_retriever(similarity_top_k=3,
                                                       retriever_mode=ListRetrieverMode.EMBEDDING)
        user_chat_history = self.get_user_chat_history(context)

        used_context = ''
        context_messages = []
        while len(resource_queries):
            q = resource_queries.pop(0)
            if q.query_type == 'content':
                assert isinstance(q, QaResourceStringQuery)
                search_result = content_query_engine.retrieve(q.query)
                for node in search_result:
                    context_messages.append(f'Search result for "{q.query}" from content: ' + node.text)
                if search_result:
                    used_context = max(search_result, key=lambda n: n.score).text
            elif q.query_type == 'slide_image' and 'image_des' in extra_data:
                assert isinstance(q, QaResourceStringQuery)
                image_desc = self._describe_image(q.query, extra_data['image_des'])
                context_messages.append(f'Currently the video shows a slide with the '
                                        f'following information: {image_desc}.')
            elif q.query_type == 'chat_history_content':
                assert isinstance(q, QaResourceStringQuery)
                msgs_as_docs = [Document(text=f'At {entry.msg_time.strftime(SIMPLE_DT_FORMAT)},'
                                              f' {entry.sender} said: "{entry.msg}"')
                                for entry in user_chat_history]
                user_msgs_vs = VectorStoreIndex.from_documents(msgs_as_docs, similarity_top_k=10)
                search_result = user_msgs_vs.as_retriever().retrieve(q.query)
                for node in search_result:
                    context_messages.append(f'Search result for "{q.query}" from chat history: ' + node.text)
                # Always execute a content search too (for now) in case there was a misunderstanding
                resource_queries.append(QaResourceStringQuery('content', q.query))
            elif q.query_type == 'chat_time_range':
                assert isinstance(q, QaResourceDatetimeQuery)
                relevant_entries = [
                    entry for entry in user_chat_history
                    if q.range_start <= entry.msg_time <= q.range_end
                ]
                if len(relevant_entries) == 0:
                    resource_queries.append(QaResourceStringQuery('content', q.nl_range))
                # Keeping out date for now(?)
                context_messages.append(f'Chat messages retrieved for query "{q.nl_range}" (resolved: '
                                        f'{q.range_start.strftime(SIMPLE_DT_FORMAT)} '
                                        f'to {q.range_end.strftime(SIMPLE_DT_FORMAT)}:\n' +
                                        '\n'.join(f'{r.sender}: {r.msg}' for r in relevant_entries))
        return context_messages, used_context

    def _answer_question(self, question: str, context_messages: List[str], context: BotRequestContext) -> str:
        cfg = context.get(self._selected_app)
        chat_history = context.get(self._chat_history)
        chat_history.append(ChatMessage(role=MessageRole.USER, content=question))

        context_str = '\n\n'.join(context_messages)
        all_messages = (
                [ChatMessage(role=MessageRole.SYSTEM,
                             content=cfg.system_prompt)]
                + [ChatMessage(role=MessageRole.USER,
                               content=cfg.context_template.replace('{context_str}', context_str))]
                + [ChatMessage(role=MessageRole.ASSISTANT,
                               content=self.get_startup_message(context))]
                + chat_history
        )
        memory = ChatMemoryBuffer.from_defaults(token_limit=6000)
        memory.set(all_messages)
        response = self._llm.chat(
            memory.get()  # Purpose is to trim to the token limit
        )
        answer = response.message.content

        chat_history.append(ChatMessage(role=MessageRole.ASSISTANT, content=answer))
        context.store(self._chat_history, chat_history)
        self.append_to_user_chat_history(context, question, answer)

        return answer

    def _describe_image(self, question, image) -> str:  # Updates the question

        image_description = explain_image(
            image_string=image, prompt=question
        )
        print(
            "\nImage description by VLM:\n", image_description
        )
        return image_description

    def get_startup_message(self, context: BotRequestContext):
        return context.get(self._selected_app).start_message

    def config_for_offline_session(
            self,
            bot_name: Optional[str],
            archive_dir: str,
            context: BotRequestContext,
            system_prompt: str = ""
    ):
        if bot_name is not None and bot_name in self._apps:
            selected_app = self._apps[bot_name]
        else:
            selected_app = self._apps[self._default_app_name]
        
        # Can replace default system prompt.
        if system_prompt:
            selected_app.system_prompt = system_prompt     

        context.store(self._selected_app, selected_app)

        # Important to call super
        super().config_for_offline_session(bot_name, archive_dir, context)

    @override
    def _initialize_vectorstore(self, full_content: str, vector_idx_dir: Path):
        print("Building vectorstore at", vector_idx_dir)
        document = Document(
            text=full_content,
        )
        vector_store_index = VectorStoreIndex.from_documents(
            [document], similarity_top_k=5
        )
        vector_store_index.storage_context.persist(persist_dir=vector_idx_dir)
        print(" -> finished Building vectorstore at", vector_idx_dir)

    @override
    def _extend_existing_vectorstore(self, new_content: str, vector_idx_dir: Path):
        storage_context = StorageContext.from_defaults(persist_dir=str(vector_idx_dir))
        vector_store_index = load_index_from_storage(storage_context)
        new_doc = Document(text=" " + new_content)
        vector_store_index.insert(new_doc)
        vector_store_index.storage_context.persist(persist_dir=vector_idx_dir)


def explain_image(image_string, prompt):
    url = "http://192.168.0.62:11435/api/generate"

    system_prompt = ("You are given a slide from a lecture, together with a question from the user. Provide a "
                     "detailed, objective description of the slide without any interpretation. Here is the users "
                     "question:\n")

    complete_prompt = system_prompt + prompt
    # Define the payload
    payload = {
        "model": "minicpm-v",
        "prompt": complete_prompt,
        "stream": False,
        "images": [image_string],  # Replace with full base64 string
    }

    # Define headers
    headers = {"Content-Type": "application/json"}

    # Make the POST request
    try:
        response = requests.post(url, json=payload, headers=headers)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse and print the JSON response
            print("Response from server:", response.json())
            return response.json()["response"]
        else:
            # Handle unsuccessful responses
            print(f"Error: Received status code {response.status_code}")
            print(response.text)
            return "None"
    except requests.exceptions.RequestException as e:
        # Handle request exceptions
        print(f"An error occurred: {e}")
