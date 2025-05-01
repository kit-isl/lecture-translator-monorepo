from typing import Optional, List

from .base import BotBase, BotRequestContext, PropertyKey as K
from .base import BotResponse


class ServerTestingFakeBot(BotBase):

    def __init__(self):
        self._log: K[List[str]] = K('fake_bot_log', List[str], scope='session', default=[])

    def _add_to_protocol(self, msg: str, context: BotRequestContext):
        log = context.get(self._log)
        log.append(msg)
        context.store(self._log, log)

    def config_for_offline_session(self, bot_name: Optional[str], archive_dir: str, context: BotRequestContext):
        self._add_to_protocol(f'config for for offline session {bot_name} with archive dir {archive_dir}', context)

    def init_online_session(self, directory: str, context: BotRequestContext):
        self._add_to_protocol(f'init online session in {directory}', context)

    def update_online_session_content(self, new_content: str, context: BotRequestContext):
        self._add_to_protocol(f'update online session content: {new_content}', context)

    def finish_session(self, context: BotRequestContext):
        self._add_to_protocol(f'finish session', context)

    def answer_chat_message(self, user_message: str, context: BotRequestContext, additional_data: dict) -> BotResponse:
        log = context.get(self._log)
        context.store(self._log, [])
        return BotResponse(
            f'Bot answering to this question: "{user_message}"\n'
            f'I received the following messages before:\n'
            + '\n- '.join(log)
        )
