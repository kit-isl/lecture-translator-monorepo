from pathlib import Path

from pydantic import BaseModel

from bot.llama_index_bot import LlamaIndexBot
from .base import BotRequestContext, PropertyKey as K
from typing import List, Dict, Optional


class TestContext(BotRequestContext):

    def __init__(self):
        super().__init__()
        self.values = {}
        self.user_id = 'test-test-com'
        self.session_id = '21'
        self.stream_id = '42'

    def _get_str(self, key: str, scope) -> Optional[str]:
        return self.values.get(key)

    def _store_str(self, key: str, value: Optional[str], scope):
        if value is None:
            if key in self.values:
                del self.values[key]
        else:
            self.values[key] = value


class TestModel(BaseModel):
    val1: str
    val2: List[str]
    val3: Optional[int] = None


def test_string_conversion():
    context = TestContext()
    test_cases = [
        (K('k1', str), 'test string'),
        (K('k2', int), 42),
        (K('k3', float), 42.0),
        (K('k4', Path), Path('test_path')),
        (K('k5', List[str]), ['val1', 'val2', 'val3']),
        (K('k6', List[str]), []),
        (K('k7', List[int]), [42, 43, 21]),
        (K('k8', Dict[str, List[str]]), {'key': ['1', '2']}),
        (K('k9', TestModel), TestModel(val1='test', val2=['x', 'y'])),
        (K('k10', List[TestModel]), [TestModel(val1='test', val2=['x', 'y'], val3=42),
                                     TestModel(val1='test2', val2=['a', 'b'])]),
    ]
    for key, value in test_cases:
        context.store(key, value)
        assert context.get(key) == value
    for key, _ in test_cases:
        context.store(key, None)
        assert context.get(key) is None


def test_context_chat_prompting():
    Path('tmp').mkdir(exist_ok=True)
    context = TestContext()
    bot = LlamaIndexBot('tmp/vs', 'tmp/chats')
    print(bot._answer_question('What is Chet-GPT',
                               [
                                   'Search result for "Chet-GPT" from content: '
                                   'It is a model that everyone is using at the moment.'
                               ], context))
    print(bot._answer_question('What did we talk about yesterday',
                               [
                                   'Chat messages for query "yesterday" (resolved: 2024-11-12 00:00:00 '
                                   'to 2024-11-12 23:59:59):\n'
                                   'user: What is the meaning of tau meson?\n'
                                   'ai: The tau (τ), also called the tau lepton, tau particle or tauon, '
                                   'is an elementary particle similar to the electron, with negative electric charge '
                                   'and a spin of 1/2\n'
                                   'user: When did we last talk about spin?\n'
                                   'ai: We last chatted about spin on 2024-11-07.'
                               ], context))
    print(bot._answer_question('What did we say about A star algorithm',
                               [
                                   'Search result for "A star algorithm" from chat history: '
                                   'We discussed implementation details of A* at 2024-12-12.'
                               ], context))
    context = TestContext()  # reset chat
    print(bot._answer_question('What is the tau meson and how does it relate to what we talked about this morning?',
                               [
                                   'Search result for "tau meson" from content: '
                                   'The tau (τ), also called the tau lepton, tau particle or tauon, '
                                   'is an elementary particle similar to the electron, with negative electric charge '
                                   'and a spin of 1/2',
                                   'Chat messages for query "this morning" (resolved: 2024-11-07 00:00:00 '
                                   'to 2024-11-07 12:00:00):\n'
                                   'user: Can you explain spin?\n'
                                   'ai: Spin is an intrinsic form of angular momentum carried by elementary '
                                   'particles, and thus by composite particles such as hadrons, atomic nuclei, '
                                   'and atoms.'
                               ], context))


if __name__ == '__main__':
    test_string_conversion()
    test_context_chat_prompting()
