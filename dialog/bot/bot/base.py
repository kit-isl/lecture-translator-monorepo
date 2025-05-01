import json
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Type, TypeVar, Generic, Literal, Callable

import pydantic

_T = TypeVar('_T')

PropertyScope = Literal['session', 'stream']


@dataclass
class PropertyKey(Generic[_T]):
    key: str
    type: Type[_T]
    scope: PropertyScope = 'stream'
    default: _T = None


class BotRequestContext(ABC):
    user_id: Optional[str]
    """
    Uniquely identifying the logged-in user, or None if the user is not logged in.
    If this is None, this may disable some features (e.g. permanent chat history).
    """

    session_id: str
    """The LT session this request belongs to."""

    stream_id: str
    """The stream inside the LT session this request belongs to."""

    def get(self, key: PropertyKey[_T]) -> Optional[_T]:
        str_val = self._get_str(key.key, key.scope)
        if str_val is None:
            return key.default() if isinstance(key.default, Callable) else key.default
        t = getattr(key.type, '__origin__', key.type)
        if t in [list, dict]:
            content_type = getattr(key.type, '__args__', None)
            if t == list and content_type and issubclass(content_type[0], pydantic.BaseModel):
                return [content_type[0].model_validate(x) for x in json.loads(str_val)]
            else:
                return json.loads(str_val)
        elif issubclass(key.type, pydantic.BaseModel):
            return key.type.model_validate_json(str_val)
        elif key.type in [int, float, str, bool, Path]:
            return key.type(str_val)
        else:
            raise NotImplementedError(f'Type {key.type} not supported yet')

    def store(self, key: PropertyKey[_T], value: _T):
        if value is None:
            self._store_str(key.key, None, key.scope)
            return

        t = getattr(key.type, '__origin__', key.type)
        if t in [list, dict]:
            if isinstance(value, list):
                value = [x.model_dump() if isinstance(x, pydantic.BaseModel) else x for x in value]
            value = json.dumps(value)
        elif key.type in [int, float, str, bool, Path]:
            value = str(value)
        elif isinstance(value, pydantic.BaseModel):
            value = value.model_dump_json()
        else:
            raise NotImplementedError(f'Type {key.type} not supported yet')
        self._store_str(key.key, value, key.scope)

    def _get_str(self, key: str, scope: PropertyScope) -> Optional[str]:
        raise NotImplementedError

    def _store_str(self, key: str, value: Optional[str], scope: PropertyScope):
        raise NotImplementedError


@dataclass
class BotResponse:
    answer: str
    context: Optional[str] = None  # The RAG context used to answer the question


class BotBase(ABC):

    def answer_chat_message(self, user_message: str, context: BotRequestContext,
                            additional_data: dict) -> BotResponse:
        """
        Answer the given chat message using this Bot.
        This method can have side effects, e.g. storing the message in some message history.
        The context can be used to disambiguate requests.
        additional_data is the LT message, in case there is extra information necessary (e.g. image).
        """
        raise NotImplementedError

    def config_for_offline_session(
            self,
            bot_name: Optional[str],
            archive_dir: str,
            context: BotRequestContext
    ):
        """
        This is called to configure a bot for an offline session (archived lecture/meeting, simple static bot).
        This includes initializing vector stores etc.
        Implementation should check whether it needs to build a new index or a load an existing index.
        Consequently, this method may have the side effect of building a new index.
        """
        pass

    def init_online_session(self, directory: Optional[str], context: BotRequestContext):
        """
        This is called to initialize an online session from the lecturer (stream 0).
        The directory is where to save the content after finalizing the session.
        directory can be None if the session should not be stored in any way.
        """
        pass

    def update_online_session_content(self, new_content: str, context: BotRequestContext):
        """
        This is called to update vector stores etc. given the new content in an online session.
        Implementation will add this to the index / create a new index.
        """
        pass

    def finish_session(self, context: BotRequestContext):
        pass

    def get_startup_message(self, context: BotRequestContext):
        return 'Hi!\nI am your AI assistant. How can I help you?'
