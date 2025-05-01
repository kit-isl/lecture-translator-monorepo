import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal

from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.llms import LLM

SIMPLE_DT_FORMAT = '%Y-%m-%d %H:%M:%S'


@dataclass
class QaResourceQuery:
    query_type: Literal['content', 'chat_history_content', 'chat_time_range', 'slide_image']


@dataclass
class QaResourceStringQuery(QaResourceQuery):
    query_type: Literal['content', 'chat_history_content', 'slide_image']
    query: str


@dataclass
class QaResourceDatetimeQuery(QaResourceQuery):
    query_type: Literal['chat_time_range']
    nl_range: str
    range_start: datetime
    range_end: datetime


class QaResourceAnalyzer:
    """
    Responsible for analyzing a question, and determining what data sources need to be retrieved to answer the question.
    """

    def __init__(self, llm: LLM):
        super().__init__()
        self.llm = llm
        self._prompt_prefix = [
            ChatMessage(role=MessageRole.SYSTEM,
                        content='Decide what data you need to retrieve to answer the given question. '
                                'Your output should be a json list of queries to the given data sources. '
                                'Either {"type": "content", "query": "..."} to retrieve from the lecture content, '
                                'or {"type": "chat_history_content", "query": "..."} to retrieve from previous'
                                ' conversations with the user based on the conversation content, '
                                'or {"type": "chat_time_range", "range_start": "YYYY-MM-DD HH-mm-ss",'
                                ' "range_end": "YYYY-MM-DD HH-mm-ss"} to retrieve from previous conversations based'
                                ' on their date and time,'
                                'or {"type": "slide_image", "query": "..."} to take a look at the slide image'
                                ' (only if the question requires it). '
                                'Make sure to apply commonsense for vague instructions,'
                                ' better overestimate the range instead of having it too tight.'
                                'Output only a JSON list of one or more queries, no other content.'),
            ChatMessage(role=MessageRole.USER,
                        content='Explain to me how the inner loop works in detail'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "content", "query": "how does the inner loop work in detail"}]'),
            ChatMessage(role=MessageRole.USER,
                        content='What did we say about the handling of the variables?'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "chat_history_content", "query": "handling of the variables"}]'),
            ChatMessage(role=MessageRole.USER,
                        content='When did we talk about the definition of A star?'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "chat_history_content", "query": "definition of A star"}]'),
            ChatMessage(role=MessageRole.USER,
                        content='What does the slide say about content retrieval?'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "slide_image", "query": "content retrieval"}]'),
            ChatMessage(role=MessageRole.USER,
                        content='What did we talk about yesterday and today'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "chat_time_range", "nl_range": "yesterday and today", '
                                '"range_start": "2024-07-13 00:00:00", '
                                '"range_end": "2024-07-14 23:59:59"}]'),
            ChatMessage(role=MessageRole.USER,
                        content='What is the properties of tau meson'
                                ' and how does it relate to what we talked about this morning?'),
            ChatMessage(role=MessageRole.ASSISTANT,
                        content='[{"type": "content", "query": "properties of tau meson"}, '
                                '{"type": "chat_time_range", "nl_range": "this morning", '
                                '"range_start": "2023-12-01 00:00:00", '
                                '"range_end": "2023-12-01 12:00:00"}]'),
        ]

    def analyze(self, user_query: str) -> List[QaResourceQuery]:
        messages = self._prompt_prefix + [
            ChatMessage(role=MessageRole.USER, content="Today is " + datetime.now().strftime(SIMPLE_DT_FORMAT)),
            ChatMessage(role=MessageRole.USER, content=user_query),
        ]
        response = self.llm.chat(messages)
        text_response = response.message.content
        try:
            return self._parse_response(text_response)
        except:
            print('Failed to analyze query', user_query)
            traceback.print_exc()
            return [QaResourceStringQuery('content', user_query)]  # Fallback to default lecture content RAG

    def _parse_response(self, response: str) -> List[QaResourceQuery]:
        print('Plain response:', response)
        text_response = _drop_non_json_list_clutter(response)
        print('Cleaned response:', text_response)
        output_list = json.loads(text_response)
        queries = []
        for output_query in output_list:
            if 'type' not in output_query:
                continue
            query_type = output_query['type']
            if query_type in ('content', 'chat_history_content', 'slide_image') and 'query' in output_query:
                queries.append(QaResourceStringQuery(query_type, output_query['query']))
            elif query_type == 'chat_time_range' and 'range_start' in output_query and 'range_end' in output_query:
                range_start = datetime.strptime(output_query['range_start'], SIMPLE_DT_FORMAT)
                range_end = datetime.strptime(output_query['range_end'], SIMPLE_DT_FORMAT)
                queries.append(QaResourceDatetimeQuery('chat_time_range', output_query.get('nl_range', ''),
                                                       range_start, range_end))
        return queries


def _drop_non_json_list_clutter(output: str):
    first_bracket = output.find('[')
    if first_bracket > -1:
        output = output[first_bracket:]
    last_bracket = output.rfind(']')
    if last_bracket > -1:
        output = output[:last_bracket + 1]
    return output
