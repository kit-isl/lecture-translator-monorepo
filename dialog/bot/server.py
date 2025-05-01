#!/usr/bin/env python3
import argparse
import json
import os
import re
import traceback
import urllib.parse
from typing import Optional

import jsons
from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database

from bot.base import BotRequestContext, BotResponse, BotBase, PropertyScope
from bot.llama_index_bot import LlamaIndexBot
from bot.response_decision import ResponseDecisionModule
from bot.test_fake_bot import ServerTestingFakeBot

property_to_default = {"datasource": "asr:0", "server": "http://192.168.0.65:5051"}

FALLBACK_VECTORSTORE_STORAGE_DIR: str = "/vs/"
USER_HISTORY_STORAGE_DIR: str = "/chats/"


class LTRequestContext(BotRequestContext):
    def __init__(self, session_id: str, stream_id: str, user_id: str):
        super().__init__()
        self.session_id = session_id
        self.stream_id = stream_id
        self.user_id = user_id

    @classmethod
    def from_lt_message(cls, data: dict):
        session = data["session"]
        stream = data["tag"]
        user = data.get("user")
        if user == 'None':  # Some upstream component sends None erroneously as a string
            user = None
        if user:
            # Replace all special characters with "-" to make it filename-safe
            user = re.sub(r'\W', '-', user)
        return cls(session, stream, user)

    def _context_for_scope(self, scope: PropertyScope):
        if scope == 'stream':
            return [_name, self.session_id, self.stream_id]
        elif scope == 'session':
            return [_name, self.session_id, "0"]
        else:
            raise NotImplementedError(f'Unsupported scope "{scope}"')

    def _get_str(self, key: str, scope: PropertyScope) -> Optional[str]:
        return db.getPropertyValues(key, context=self._context_for_scope(scope))

    def _store_str(self, key: str, value: Optional[str], scope: PropertyScope):
        db.setPropertyValue(key, value, context=self._context_for_scope(scope))


class BotServer:

    def __init__(self, name, use_fake_bot=False):
        self.name = name

        self.bot: BotBase = ServerTestingFakeBot() if use_fake_bot else LlamaIndexBot(
            fallback_vectorstore_dir=FALLBACK_VECTORSTORE_STORAGE_DIR,
            user_chat_history_dir=USER_HISTORY_STORAGE_DIR)
        self.response_decision_module = ResponseDecisionModule()

    def _invoke_bot(self, question, data):
        context = LTRequestContext.from_lt_message(data)
        try:
            response = self.bot.answer_chat_message(question, context, data)
        except Exception as e:
            print('Error in bot', e)
            traceback.print_exc()
            response = BotResponse('Sorry, I cannot answer this question with this lecture as context')

        answer = response.answer.strip('"')
        print("Answer: ", answer)
        if answer != "":
            data["sender"] = self.name + ":" + context.stream_id
            data["seq"] = answer
            data["context"] = response.context
            con.publish("mediator", context.session_id + " " + context.stream_id, jsons.dumps(data))

    @staticmethod
    def _aggregate_stream_0_seq_messages(messages: list[str]) -> list[dict]:
        """
        Aggregates incoming messages. Merges the "seq" fields to allow more batched processing of incoming text.
        This prevents the vector store building process being unnecessarily slow by preventing many short texts getting added one after another.
        System messages (the ASR output) are always put first in the list.
        :param myParam2: List of incoming messages.
        :return: The list of incoming messages after aggregation.
        """
        system_messages: dict = {}
        new_messages: list[dict] = []
        for message in messages:
            data = json.loads(message)
            if "seq" in data and data["tag"] == "0":
                if system_messages == {}:
                    system_messages = data
                else:
                    system_messages["seq"] += data["seq"]
            else:
                new_messages.append(data)
        if system_messages != {}:
            new_messages.insert(0, system_messages)
        return new_messages

    def _handle_control_msg(self, data: dict):
        context = LTRequestContext.from_lt_message(data)
        cmd = data["controll"]
        
        if cmd == "START":
            # The system prompt can be changed by the client.
            system_prompt: str = data.get("system_prompt")
            
            if "content_directory" in data:  # content_directory => offline session
                directory = urllib.parse.unquote_plus(urllib.parse.unquote_plus(data["content_directory"]))
                self.bot.config_for_offline_session(data.get('bot'), directory, context, system_prompt)
            else:
                # 'directory' can be missing, meaning online session that should not be saved
                # no content_directory -> online session (should not be saved) or special bot session.
                if not data.get('bot'):
                    online_storage_directory = data.get('directory')
                    self.bot.init_online_session(online_storage_directory, context)
                else:
                    self.bot.config_for_offline_session(data.get('bot'), "", context, system_prompt)

            data["sender"] = self.name + ":" + context.stream_id
            data["seq"] = self.bot.get_startup_message(context)
            
            con.publish(
                "mediator", context.session_id, jsons.dumps(data)
            )  # Send START to further components
        
        elif cmd == "END":
            self.bot.finish_session(context)
            data["sender"] = self.name + ":" + context.stream_id
            con.publish("mediator", context.session_id, jsons.dumps(data))
        elif cmd == "INFORMATION":
            properties = db.getPropertyValues()
            data = {
                "session": context.session_id,
                "controll": "INFORMATION",
                "sender": self.name + ":" + context.stream_id,
                self.name + ":" + context.stream_id: properties,
            }
            con.publish("mediator", context.session_id, jsons.dumps(data))

    def processAggregate(self, mList):
        try:
            self._processAggregate_impl(mList)
        except BaseException as e:
            print(f'Failed to handle messages ({e}):', mList)
            traceback.print_exc()

    def _processAggregate_impl(self, mList):
        print("mList", mList)
        messages = self._aggregate_stream_0_seq_messages(mList)
        print("messages", messages)

        # Process mList
        for data in messages:
            print("data: ", data)
            context = LTRequestContext.from_lt_message(data)

            if "controll" in data:
                self._handle_control_msg(data)
            else:  # This message appears after initializing all streams with control start
                if "unstable" in data and data["unstable"] is True:  # Ignore Unstable outputs and process only Stable
                    pass
                else:
                    if context.stream_id == "0":
                        # ADD Text to content if stream is 0 indicating not direct input for BOT
                        self.bot.update_online_session_content(data["seq"], context)
                    else:  # Stream more than 0 indicates user no. stream is sending this via ASR, MT or TextBox
                        current_input = data["seq"].strip()

                        # The stream !=0 and sender is user indicates Chat Box message.
                        #  CAN CHANGE BUT UPDATE ACCORDINGLY
                        input_received_from_chat = data["sender"].startswith("user")
                        print("Input received to the bot from:", input_received_from_chat)

                        if input_received_from_chat:  # Input from Text box directly sent to LLM
                            self._invoke_bot(current_input, data)
                        else:
                            response_decision = self.response_decision_module.decide(current_input, context)
                            if response_decision != "WAIT":  # None indicates WAIT
                                self._invoke_bot(current_input, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queue-server", help="NATS Server address", type=str, default="localhost"
    )
    parser.add_argument(
        "--queue-port", help="NATS Server address", type=str, default="5672"
    )
    parser.add_argument(
        "--redis-server", help="NATS Server address", type=str, default="localhost"
    )
    parser.add_argument(
        "--redis-port", help="NATS Server address", type=str, default="6379"
    )
    parser.add_argument(
        "--use-test-bot", help="Use the ServerTestingFakeBot implementation",
        action="store_true", default=False
    )
    args = parser.parse_args()

    _name = "bot"

    print("Initialize the bot with", args)

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    d = {"languages": []}

    con = get_best_connector()(args.queue_server, args.queue_port, db)
    con.register(db, _name, d)

    queue = os.getenv("QUEUE_SYSTEM")
    if queue == "KAFKA" or queue == "KAFKA-P":
        bot = BotServer(_name, use_fake_bot=args.use_test_bot)
        con.consume(_name, bot, True)
