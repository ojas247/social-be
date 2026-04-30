from fastapi import FastAPI, Body, Request
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
import uuid
import httpx
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
from google.genai import types as genai_types
from a2ui.schema.constants import VERSION_0_8, VERSION_0_9
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog


from app.rest_agent import get_restaurants


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
    version=VERSION_0_8, # Use VERSION_0_9 for newer protocol
    catalogs=[
        BasicCatalog.get_config(
            version=VERSION_0_8, examples_path="examples/0.8"
        )
    ],
)

# Generate the full system prompt
A2UI_AND_AGENT_INSTRUCTION = schema_manager.generate_system_prompt(
    role_description=ROLE_DESCRIPTION,
    ui_description=UI_DESCRIPTION,
    include_schema=True,
    include_examples=True,
    validate_examples=True,
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
    
        # Extract your custom envelope fields
    app_name   = body.get("appName", "app")
    user_id    = body.get("userId", "user")
    session_id = body.get("sessionId", "session")

    # Extract the message text from the simplified newMessage schema
    new_message = body.get("newMessage", {})
    parts       = new_message.get("parts", [])
    user_text   = parts[0].get("text", "") if parts else ""

    # Ensure session exists
    existing = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    runner = Runner(
        agent=root_agent,
        app_name=app_name,
        session_service=session_service,
    )

    adk_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_text)],
    )


    async def event_stream():
        final_response_content = ""
        
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=adk_message,
            ):
                if not event.content or not event.content.parts:
                    continue

                if event.is_final_response():
                    # Extract the full text
                    final_response_content = "\n".join(
                        [p.text for p in event.content.parts if p.text]
                    )
                    # Yield the final completed state
                    yield f"data: {json.dumps({'is_task_complete': True, 'content': final_response_content})}\n\n"
                    break 
                else:
                    # Yield intermediate status
                    yield f"data: {json.dumps({'is_task_complete': False, 'updates': 'Processing...'})}\n\n"
        
        except Exception as e:
            yield f"data: {json.dumps({'is_task_complete': True, 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )




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