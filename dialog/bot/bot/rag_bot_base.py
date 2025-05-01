import json
import traceback
from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Literal

from pydantic import BaseModel

from .base import BotBase, BotRequestContext, PropertyKey as K


class ChatHistoryEntry(BaseModel):
    msg_time: datetime
    sender: Literal['ai', 'user']
    msg: str


class LTRagBotBase(BotBase, ABC):
    """
    Base class for bots that support RAG with both offline (LT archive) and online mode.
    Also handles user-specific chat history loading/saving.
    """

    def __init__(self,
                 fallback_vectorstore_dir: str,
                 user_chat_history_dir: str,
                 ):
        super().__init__()
        self._fallback_vectorstore_dir = Path(fallback_vectorstore_dir)
        self._user_chat_history_dir = Path(user_chat_history_dir)
        self._fallback_vectorstore_dir.mkdir(exist_ok=True)
        self._user_chat_history_dir.mkdir(exist_ok=True)

        # Only set for offline sessions.
        # This needs to be session scope as it defines the vector_idx_dir, thus also affects bot QA
        self._archive_dir: K[Path] = K('archive_dir', Path, scope='session')

        # Only set for online sessions (and only accessed by stream 0, thus stream scope is ok)
        self._online_session_output_dir: K[Path] = K('online_session_output_dir', Path, scope='session')
        self._session_content: K[str] = K('content', str, default='')

    def get_vector_idx_dir(self, context: BotRequestContext) -> List[Path]:
        """
        Side-effect-free function that determines the vector idx dir(s) to use given the current context
        (without changing/creating anything).
        It's only more than one in case the archive dir is a lecture directory that contains multiple lectures.
        """
        archive_dir = context.get(self._archive_dir)
        if archive_dir:
            if (archive_dir / 'sessionGraph').is_file() or (archive_dir / 'bot').is_file():
                return [archive_dir / 'vectorStore']
            return [
                sub_dir / 'vectorStore'
                for sub_dir in archive_dir.iterdir()
                if sub_dir.is_dir()
                and (sub_dir / 'sessionGraph').is_file()
                and (sub_dir / 'vectorStore').is_dir()
            ]

        online_session_dir = context.get(self._online_session_output_dir)
        if online_session_dir:
            return [online_session_dir / 'vectorStore']
        else:
            # Online session without saving / unauthenticated
            return [self._fallback_vectorstore_dir / context.session_id]

    def get_user_chat_history(self, context: BotRequestContext) -> List[ChatHistoryEntry]:
        if not context.user_id:
            return []
        history_path = self._user_chat_history_dir / context.user_id / 'chat_history.json'
        if not history_path.is_file():
            return []
        messages = json.loads(history_path.read_text())
        return [
            ChatHistoryEntry.model_validate(msg)
            for msg in messages
        ]

    def append_to_user_chat_history(self, context: BotRequestContext, user_msg: str, ai_msg: str):
        if not context.user_id:
            return
        history = self.get_user_chat_history(context)
        now = datetime.now()
        history.append(ChatHistoryEntry(msg_time=now, sender='user', msg=user_msg))
        history.append(ChatHistoryEntry(msg_time=now, sender='ai', msg=ai_msg))
        user_dir = self._user_chat_history_dir / context.user_id
        user_dir.mkdir(exist_ok=True)
        history_file = user_dir / 'chat_history.json'
        history_file.write_text(json.dumps([
            msg.model_dump(mode='json')
            for msg in history
        ]))

    def config_for_offline_session(self, bot_name: Optional[str], archive_dir: str, context: BotRequestContext):
        # archive_dir empty? Set to predefined dir.
        if not archive_dir:
            archive_dir = self._apps[bot_name].content_directory
        
        archive_dir = Path(archive_dir)
        context.store(self._archive_dir, archive_dir)

        # if necessary, create vectorstore from archive
        vector_idx_dirs = self.get_vector_idx_dir(context)
        print("vector_idx_dirs: ", str(vector_idx_dirs))
        for vector_idx_dir in vector_idx_dirs:
            if vector_idx_dir.is_dir():
                print("Found existing vector_index_dir: ", vector_idx_dir)
                # Nothing to do, vectorstore already exists
            else:
                bot_file = archive_dir / "bot"
                if bot_file.is_file():
                    content = bot_file.read_text()
                else:
                    # This is a directory session about multiple lectures.
                    #  Some lecture apparently has not vectorStore yet.
                    #  We ignore it for now, the user should open it separately to create the vectorStore
                    content = None
                    
                if content:
                    self._initialize_vectorstore(content, vector_idx_dir)

    def init_online_session(self, directory: Optional[str], context: BotRequestContext):
        if directory:
            context.store(self._online_session_output_dir, Path(directory))

    def update_online_session_content(self, new_content: str, context: BotRequestContext):
        content = context.get(self._session_content)
        content += " " + new_content
        context.store(self._session_content, content)

        vector_idx_dirs = self.get_vector_idx_dir(context)
        assert len(vector_idx_dirs) == 1, str(vector_idx_dirs)
        vector_idx_dir = vector_idx_dirs[0]
        try:
            if vector_idx_dir.is_dir():
                self._extend_existing_vectorstore(new_content, vector_idx_dir)
            else:
                self._initialize_vectorstore(new_content, vector_idx_dir)
        except Exception as e:
            print('Failed to write Vectorstore', vector_idx_dir, str(e))
            traceback.print_exc()

    def finish_session(self, context: BotRequestContext):
        online_session_dir = context.get(self._online_session_output_dir)
        if online_session_dir and online_session_dir.is_dir():
            print("Write to dir:", online_session_dir)
            content = context.get(self._session_content)
            (online_session_dir / 'bot').write_text(content)

    def _initialize_vectorstore(self, full_content: str, vector_idx_dir: Path):
        raise NotImplementedError

    def _extend_existing_vectorstore(self, new_content: str, vector_idx_dir: Path):
        raise NotImplementedError
