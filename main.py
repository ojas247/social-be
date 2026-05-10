from fastapi import FastAPI, Body, Request
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
import uuid
import httpx
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from app.agents.agent import RestaurantAgent
from app.agents.agent_executor import RestaurantAgentExecutor

from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskStore
from a2a.server.agent_execution import RequestContext
from a2a import types as a2a_types
from a2a.types import MessageSendParams, TaskStatusUpdateEvent, Message, Role, TextPart


from app.routes.tweet_routes import router as tweet_router
from app.routes.auth_routes import router as auth_router
from app.routes.agents_routes import router as agents_router
import os
import json
import uuid
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.llm_agent import Agent
from google.adk.tools.tool_context import ToolContext

from google.genai import types as genai_types
from a2ui.schema.constants import VERSION_0_8, VERSION_0_9
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog


from app.rest_agent import get_restaurants

## Test
def _parse_adk_text_to_a2a_parts(content: str) -> list[a2a_types.Part]:
    """
    Converts an ADK/LLM text response into A2A parts.

    Supports two formats:
    - Your current delimiter format: conversational text + `---a2ui_JSON---` + raw JSON (list/object)
    - A2UI tag format: <a2ui-json>...</a2ui-json> (handled by a2ui parser)
    """
    content = (content or "").strip()
    if not content:
        return []

    def _sanitize_json_string(s: str) -> str:
        s = (s or "").strip()
        # Strip A2UI tags if the model included them.
        # Some model outputs put only one of the tags in the "JSON" segment.
        s = s.replace("<a2ui-json>", "").replace("</a2ui-json>", "").strip()
        if s.startswith("```json"):
            s = s[len("```json") :]
        elif s.startswith("```"):
            s = s[len("```") :]
        if s.endswith("```"):
            s = s[: -len("```")]
        return s.strip()

    # 1) Current project delimiter format
    delimiter = "---a2ui_JSON---"
    if delimiter in content:
        before, after = content.split(delimiter, 1)
        text = before.strip()
        json_str = _sanitize_json_string(after)

        parts: list[a2a_types.Part] = []
        if text:
            parts.append(a2a_types.Part(root=a2a_types.TextPart(text=text)))

        if json_str:
            try:
                payload = json.loads(json_str)
            except Exception:
                # If JSON parsing fails, fall back to sending the raw string as text
                parts.append(a2a_types.Part(root=a2a_types.TextPart(text=json_str)))
                return parts

            messages = payload if isinstance(payload, list) else [payload]
            try:
                from a2ui.a2a.parts import create_a2ui_part
            except Exception:
                create_a2ui_part = None

            if create_a2ui_part:
                for msg in messages:
                    if isinstance(msg, dict):
                        parts.append(create_a2ui_part(msg))
            else:
                # If a2ui isn't importable in this env, just ship as generic data
                for msg in messages:
                    if isinstance(msg, dict):
                        parts.append(a2a_types.Part(root=a2a_types.DataPart(data=msg)))

        return parts

    # 1.25) If the model used <a2ui-json> tags, prefer the library parser.
    # This produces proper A2UI `data` parts (mimeType application/json+a2ui).
    if "<a2ui-json>" in content and "</a2ui-json>" in content:
        try:
            from a2ui.a2a.parts import parse_response_to_parts

            return parse_response_to_parts(content)
        except Exception:
            # Fall back to manual extraction below
            pass

    # 1.5) Embedded raw JSON payload (common model behavior)
    # e.g.
    #   "Here are results...\n[ {...}, {...} ]"
    # or just:
    #   "[ {...}, {...} ]"
    def _parts_from_json_payload(text_prefix: str, json_payload: object) -> list[a2a_types.Part]:
        out: list[a2a_types.Part] = []
        if text_prefix:
            out.append(a2a_types.Part(root=a2a_types.TextPart(text=text_prefix)))

        messages = json_payload if isinstance(json_payload, list) else [json_payload]
        try:
            from a2ui.a2a.parts import create_a2ui_part
        except Exception:
            create_a2ui_part = None

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if create_a2ui_part:
                out.append(create_a2ui_part(msg))
            else:
                out.append(a2a_types.Part(root=a2a_types.DataPart(data=msg)))
        return out

    # Try "pure JSON" first
    if content[:1] in ("[", "{"):
        try:
            payload = json.loads(_sanitize_json_string(content))
            return _parts_from_json_payload("", payload) or [
                a2a_types.Part(root=a2a_types.TextPart(text=content))
            ]
        except Exception:
            pass

    # Try "text + newline + JSON"
    for i, ch in enumerate(content):
        if ch not in ("[", "{"):
            continue
        if i == 0:
            continue
        candidate = content[i:].strip()
        if not candidate:
            continue
        try:
            payload = json.loads(_sanitize_json_string(candidate))
        except Exception:
            continue
        prefix = content[:i].strip()
        built = _parts_from_json_payload(prefix, payload)
        if built:
            return built

    # 2) A2UI tag format: <a2ui-json> ... </a2ui-json>
    # We implement a robust extractor here because some model outputs contain the tags
    # but still fail a2ui's parser (e.g., minor JSON issues). This ensures we return
    # `data` parts instead of leaving the whole block as `text`.
    open_tag = "<a2ui-json>"
    close_tag = "</a2ui-json>"
    if open_tag in content and close_tag in content:
        parts: list[a2a_types.Part] = []
        cursor = 0
        while True:
            start = content.find(open_tag, cursor)
            if start == -1:
                break
            end = content.find(close_tag, start)
            if end == -1:
                break

            before = content[cursor:start].strip()
            if before:
                parts.append(a2a_types.Part(root=a2a_types.TextPart(text=before)))

            json_block = content[start + len(open_tag) : end]
            json_block = _sanitize_json_string(json_block)

            try:
                payload = json.loads(json_block) if json_block else None
            except Exception:
                # If JSON parsing fails, keep the raw block as text (still better than crashing)
                raw = (open_tag + json_block + close_tag).strip()
                parts.append(a2a_types.Part(root=a2a_types.TextPart(text=raw)))
                cursor = end + len(close_tag)
                continue

            if payload is not None:
                messages = payload if isinstance(payload, list) else [payload]
                try:
                    from a2ui.a2a.parts import create_a2ui_part
                except Exception:
                    create_a2ui_part = None

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    if create_a2ui_part:
                        parts.append(create_a2ui_part(msg))
                    else:
                        parts.append(a2a_types.Part(root=a2a_types.DataPart(data=msg)))

            cursor = end + len(close_tag)

        trailing = content[cursor:].strip()
        if trailing:
            parts.append(a2a_types.Part(root=a2a_types.TextPart(text=trailing)))

        if parts:
            return parts

    # 3) Fallback: plain text
    return [a2a_types.Part(root=a2a_types.TextPart(text=content))]


def get_restaurants(tool_context: ToolContext) -> str:
    """Call this tool to get a list of restaurants."""
    return json.dumps([
        {
            "name": "Xi'an Famous Foods",
            "detail": "Spicy and savory hand-pulled noodles.",
            "imageUrl": "http://localhost:10002/static/shrimpchowmein.jpeg",
            "rating": "★★★★☆",
            "infoLink": "[More Info](https://www.xianfoods.com/)",
            "address": "81 St Marks Pl, New York, NY 10003"
        },
        {
            "name": "Han Dynasty",
            "detail": "Authentic Szechuan cuisine.",
            "imageUrl": "http://localhost:10002/static/mapotofu.jpeg",
            "rating": "★★★★☆",
            "infoLink": "[More Info](https://www.handynasty.net/)",
            "address": "90 3rd Ave, New York, NY 10003"
        },
        {
            "name": "RedFarm",
            "detail": "Modern Chinese with a farm-to-table approach.",
            "imageUrl": "http://localhost:10002/static/beefbroccoli.jpeg",
            "rating": "★★★★☆",
            "infoLink": "[More Info](https://www.redfarmnyc.com/)",
            "address": "529 Hudson St, New York, NY 10014"
        },
    ])

AGENT_INSTRUCTION="""
You are a helpful restaurant finding assistant. Your goal is to help users find and book restaurants using a rich UI.

To achieve this, you MUST follow this logic:

1.  **For finding restaurants:**
    a. You MUST call the `get_restaurants` tool. Extract the cuisine, location, and a specific number (`count`) of restaurants from the user's query (e.g., for "top 5 chinese places", count is 5).
    b. After receiving the data, you MUST follow the instructions precisely to generate the final a2ui UI JSON, using the appropriate UI example from the `prompt_builder.py` based on the number of restaurants."""


ROLE_DESCRIPTION = (
    "You are a helpful restaurant finding assistant. Your final output MUST be a a2ui"
    " UI JSON response."
)

WORKFLOW_DESCRIPTION = """
To generate the response, you MUST follow these rules:
1.  Your response MUST be in two parts, separated by the delimiter: `---a2ui_JSON---`.
2.  The first part is your conversational text response.
3.  The second part is a single, raw JSON object which is a list of A2UI messages.
4.  The JSON part MUST validate against the A2UI JSON SCHEMA provided below.
"""

UI_DESCRIPTION = """
-   If the query is for a list of restaurants, you MUST call `get_restaurants` and then use the returned restaurant data to populate the `dataModelUpdate.contents` array (e.g., as a `valueMap` for the "items" key).
-   If the number of restaurants is 5 or fewer, you MUST use the `SINGLE_COLUMN_LIST_EXAMPLE` template.
-   If the number of restaurants is more than 5, you MUST use the `TWO_COLUMN_LIST_EXAMPLE` template.
-   If the query is to book a restaurant (e.g., "USER_WANTS_TO_BOOK..."), you MUST use the `BOOKING_FORM_EXAMPLE` template.
-   If the query is a booking submission (e.g., "User submitted a booking..."), you MUST use the `CONFIRMATION_EXAMPLE` template.
-   If the query is anything else that does not fit into the above categories, you MUST use the `TEXT_BOX_EXAMPLE` template.
"""

load_dotenv()


instruction = f"""
{ROLE_DESCRIPTION}

### WORKFLOW:
{WORKFLOW_DESCRIPTION}

### UI REQUIREMENTS:
{UI_DESCRIPTION}
"""

# Initialize the schema manager with the Basic Catalog
schema_manager = A2uiSchemaManager(
    # IMPORTANT: Frontend is wired for v0.8 message types (beginRendering/surfaceUpdate/dataModelUpdate).
    # Using v0.9 causes the model to emit `createSurface/updateComponents/updateDataModel` + v0.9 catalogId,
    # which the frontend may not have registered.
    version=VERSION_0_8,
    catalogs=[
        BasicCatalog.get_config(
            version=VERSION_0_8, examples_path="examples/0.8"
        )
    ],
)

# Generate the full system prompt
A2UI_AND_AGENT_INSTRUCTION = schema_manager.generate_system_prompt(
    role_description=f"{AGENT_INSTRUCTION}\n\n{ROLE_DESCRIPTION}",
    workflow_description=WORKFLOW_DESCRIPTION,
    ui_description=UI_DESCRIPTION,
    # Lock the schema to v0.8 message set so the model can't emit v0.9 messages.
    allowed_messages=["beginRendering", "surfaceUpdate", "dataModelUpdate", "deleteSurface"],
    include_schema=True,
    include_examples=True,
    validate_examples=True,
)

# Google ADK treats `{var}` in instructions as session-state placeholders.
# The bundled A2UI schema text includes `${expression}`, which trips ADK's
# placeholder substitution on `{expression}`. Make it optional to avoid a hard
# failure when no such state key exists.
A2UI_AND_AGENT_INSTRUCTION = A2UI_AND_AGENT_INSTRUCTION.replace(
    "{expression}", "{expression?}"
)

# ── Agent definition ─────────────────────────────────────────────────────────
root_agent = LlmAgent(
    name="restaurant_agent",
    model="gemini-2.5-flash",
    description="An agent that finds restaurants and helps book tables.",
    instruction=A2UI_AND_AGENT_INSTRUCTION, 
    tools=[get_restaurants],
)

# ── ADK plumbing ─────────────────────────────────────────────────────────────
session_service = InMemorySessionService()

app = FastAPI()


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.post("/run")
async def run(request: Request):
    body = await request.json()
    request_id: str | int = 1
    app_name = "app"
    user_id = "user"
    session_id = str(uuid.uuid4())
    user_text = ""

    # --- Parse request -------------------------------------------------------
    if isinstance(body, dict) and body.get("jsonrpc") == "2.0" and body.get("method") == "message/send":
        req = a2a_types.SendMessageRequest.model_validate(body)
        request_id = req.id
        app_name = "app"
        user_id = "user"
        session_id = req.params.message.context_id or str(uuid.uuid4())

        for p in req.params.message.parts:
            if isinstance(p.root, a2a_types.TextPart):
                user_text = p.root.text or ""
                break
    else:
        # Simplified envelope
        if isinstance(body, dict) and "id" in body:
            request_id = body["id"]
        app_name = body.get("appName", "app") if isinstance(body, dict) else "app"
        user_id = body.get("userId", "user") if isinstance(body, dict) else "user"
        session_id = body.get("sessionId", str(uuid.uuid4())) if isinstance(body, dict) else str(uuid.uuid4())

        new_message = body.get("newMessage", {}) if isinstance(body, dict) else {}
        parts = new_message.get("parts", []) if isinstance(new_message, dict) else []
        if parts:
            user_text = (parts[0] or {}).get("text", "") if isinstance(parts[0], dict) else ""

    context_id = session_id
    task_id = str(uuid.uuid4())

    # Ensure session exists (ADK)
    existing = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    # --- Run agent and build Task -------------------------------------------
    runner = Runner(
        agent=root_agent,
        app_name=app_name,
        session_service=session_service,
    )

    history: list[a2a_types.Message] = [
        a2a_types.Message(
            context_id=context_id,
            kind="message",
            message_id=str(uuid.uuid4()),
            parts=[a2a_types.Part(root=a2a_types.TextPart(text=user_text))],
            role="user",
            task_id=task_id,
        )
    ]

    final_text = ""
    progress_fallback = "Finding restaurants that match your criteria..."

    timed_out = False
    try:
        # NOTE: This endpoint returns a single JSON response, so we must cap how long
        # we wait for the model. Increase if your model is consistently slower.
        async with asyncio.timeout(120):
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_text)],
                ),
            ):
                if not event.content or not event.content.parts:
                    continue

                chunk_text = "\n".join(
                    [p.text for p in event.content.parts if getattr(p, "text", None)]
                ).strip()

                if event.is_final_response():
                    final_text = chunk_text
                    break

                # Add intermediate "agent" messages to history (matches your desired shape)
                history.append(
                    a2a_types.Message(
                        context_id=context_id,
                        kind="message",
                        message_id=str(uuid.uuid4()),
                        parts=[
                            a2a_types.Part(
                                root=a2a_types.TextPart(
                                    text=chunk_text or progress_fallback
                                )
                            )
                        ],
                        role="agent",
                        task_id=task_id,
                    )
                )
    except TimeoutError:
        timed_out = True

    status_message = a2a_types.Message(
        context_id=context_id,
        kind="message",
        message_id=str(uuid.uuid4()),
        parts=_parse_adk_text_to_a2a_parts(final_text)
        if final_text
        else [a2a_types.Part(root=a2a_types.TextPart(text=progress_fallback))],
        role="agent",
        task_id=task_id,
    )

    # Defensive: ensure A2UI blocks become proper `data` parts.
    # If the model put A2UI JSON inside text (common), re-parse using the a2ui parser.
    if final_text:
        has_data = any(isinstance(p.root, a2a_types.DataPart) for p in status_message.parts)
        if not has_data and "<a2ui-json>" in final_text and "</a2ui-json>" in final_text:
            try:
                from a2ui.a2a.parts import parse_response_to_parts

                status_message.parts = parse_response_to_parts(final_text)
            except Exception:
                pass

    task = a2a_types.Task(
        context_id=context_id,
        history=history,
        id=task_id,
        kind="task",
        status=a2a_types.TaskStatus(
            message=status_message,
            state="working" if timed_out else "input-required",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    )

    rpc_response = a2a_types.SendMessageSuccessResponse(
        id=request_id,
        jsonrpc="2.0",
        result=task,
    )

    return JSONResponse(content=rpc_response.model_dump(exclude_none=True))




@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    # 1. Define the Skills (Required)
    # Ensure your AgentSkill model has id, name, description, and tags
    my_skill = AgentSkill(
        id="main-logic",
        name="basic_chat",
        description="The ability to hold a basic conversation.",
        tags=["general"]
    )

    # 2. Build the Card based on your source code fields
    public_agent_card = AgentCard(
        name='Recipe Agent',
        description='Agent that helps users with recipes and cooking.',
        version='1.0.0',
        url='http://localhost:8080/run', # The main endpoint
        
        # Mandatory Lists
        default_input_modes=['text'],
        default_output_modes=['text'],
        
        # Mandatory Objects
        capabilities=AgentCapabilities(
            streaming=True, 
            extended_agent_card=True
        ),
        skills=[my_skill],

        # Optional fields from your source
        icon_url='http://localhost:8080/icon.png',
        preferred_transport='JSONRPC' 
    )

    return public_agent_card