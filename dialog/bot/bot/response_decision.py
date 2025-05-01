from typing import Literal, List, Dict, Optional

from huggingface_hub import InferenceClient

from .base import BotRequestContext, PropertyKey as K


class ResponseDecisionModule:

    def __init__(self, history_window=3):
        super().__init__()

        self.history_window = history_window

        self.client = InferenceClient(
            base_url="http://192.168.0.66:8054/v1/", )

        response_decision_prompt = """You are a decision model that helps an AI assistant by generating 'ANSWER' or 'WAIT' based on the users query from the Automatic Speech Recognition (ASR) model.
        You use the dialogue flow (message history) to make your decision on if the user has completed his statement.
        The assistant answers all kinds of questions if the user is speaking to them.
        If you are not sure, then let assistant answer.
        Therefore, you have to decide when to answer and when to wait.
        If the question is finished for the assistant or you are unsure, then generate the single word 'ANSWER'.
        If the phrase is unfinished, reply with only WORD 'WAIT' and wait to answer later. Only answer with ONE WORD.
        The user can start a new conversation anytime.
        The requests from the user can come during them talking and you have to decide if the assistant should answer or wait.
            """

        self.messages = [
            {"role": "system", "content": response_decision_prompt},
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Tell me about."},
            {"role": "assistant", "content": "WAIT"},
            {"role": "user", "content": "Tell me about. The lab."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "What are few projects here?"},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Tell me more about Meetween."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Nice."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "I want to know a story about two people in Germany, going to "
                                        "football match every day for fun."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Can you tell me how many people work here?"},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "That is."},
            {"role": "assistant", "content": "WAIT"},
            {"role": "user", "content": "That is. Quite a lot of people."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Good Morning"},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "I want to know about."},
            {"role": "assistant", "content": "WAIT"},
            {"role": "user", "content": "I want to know about. the research area."},
            {"role": "assistant", "content": "ANSWER"},
            {"role": "user", "content": "Yeah, tell me."},
            {"role": "assistant", "content": "ANSWER"},
        ]

        self._running_text: K[str] = K('running_text', str, default='')
        self._req_history: K[List[Dict[str, str]]] = K('req_history', list, default=list)

    def decide(self, user_input: str,
               context: BotRequestContext) -> Optional[str]:
        """
        Takes in the current user input fragment (e.g. ASR) and decides whether to
          WAIT for further input or
          ANSWER the question now.
        Returns:
            Case WAIT: None
            Case ANSWER: the aggregated question that should be answered now
        """
        running_text = context.get(self._running_text)
        running_text += " " + user_input

        history = context.get(self._req_history)
        final_decision: Literal['ANSWER', 'WAIT']

        # Only check if we can answer if ASR/MT output ends at punctuation
        sentence_end = running_text.rstrip()[-1] not in [".", "!", "?"]
        if running_text.strip() != "" and not sentence_end:
            history.append({"role": "user", "content": running_text})
            history = history[-self.history_window:]
            messages = self.messages + history
            output = self.client.chat.completions.create(
                model="tgi",
                messages=messages,
                stream=False,
                max_tokens=5,
            )

            llm_decision = output.choices[0].message.content.strip()
            context.store(self._running_text, running_text)

            if llm_decision.lower() == "wait":
                final_decision = "WAIT"  # We keep waiting
            else:
                # The LLM says not wait and the text has punctuation at end indicating question
                #  finished according to ASR/MT
                running_text = " "  # Reset
                final_decision = "ANSWER"
            # Add response to response decision chat history
            history.append({"role": "assistant",
                            "content": final_decision})
            print("Response Decision: ", llm_decision)
            context.store(self._req_history, history)
        else:
            final_decision = 'WAIT'

        context.store(self._running_text, running_text)
        return final_decision
