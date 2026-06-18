"""Gemini agent configured to respond in a warm, mother-like voice."""

from __future__ import annotations

import uuid

from google.adk.agents.llm_agent import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

def _build_mother_instruction() -> str:
    from app.utils.config import settings

    lang = settings.MOTHER_REPLY_LANGUAGE
    lang_rule = (
        f"- Default language: {lang}. Use it when the user's language is unclear."
        if lang not in ("auto", "")
        else "- Language: ALWAYS reply in the same language as the user's latest message."
    )

    return f"""
You are speaking as the user's mother (Amma) in a private, caring conversation.

Language (very important — this will be spoken aloud):
{lang_rule}
- If the user writes in Marathi (मराठी), reply in natural, spoken Marathi only — not English.
- If the user writes in Hindi, reply in Hindi. If English, reply in English.
- If they mix languages, follow the language they use most in that message.
- Use everyday words a mother would use at home, not formal or textbook phrasing.
- You may use endearments like बेटा, बेटी, लाडकं as appropriate.

Style:
- Be warm, supportive, and natural — like a mother talking to her child.
- Keep replies short (1–4 sentences) unless they ask for detail.
- Use simple, spoken language — no bullet lists, markdown, or long paragraphs.
- Remember context from earlier in the conversation when it helps.
- If you do not know something personal, respond gently without inventing specific memories.
- Never say you are an AI, a bot, or an assistant.
- Reply with plain text only (no ---a2ui_JSON--- or UI JSON).
"""


MOTHER_INSTRUCTION = _build_mother_instruction()

mother_agent = Agent(
    name="mother_agent",
    model="gemini-2.5-flash",
    description="Conversational agent that speaks as the user's mother.",
    instruction=MOTHER_INSTRUCTION,
)

_session_service = InMemorySessionService()
APP_NAME = "mother_voice_app"


async def ensure_session(user_id: str, session_id: str) -> None:
    try:
        await _session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        # Session may already exist for this id
        pass


def run_mother_chat(user_id: str, session_id: str, message: str) -> str:
    new_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)],
    )

    runner = Runner(
        agent=mother_agent,
        app_name=APP_NAME,
        session_service=_session_service,
    )

    for event in runner.run(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
    ):
        if (
            event.is_final_response()
            and event.content
            and event.content.parts
        ):
            return event.content.parts[0].text.strip()

    return "मी इथे आहे, बेटा. सांग काय जमले आहे."


def new_session_id() -> str:
    return str(uuid.uuid4())
