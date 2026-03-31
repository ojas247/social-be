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
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskStore
from a2a.server.agent_execution import RequestContext
from a2a.types import MessageSendParams, TaskStatusUpdateEvent, Message, Role, TextPart


from app.routes.tweet_routes import router as tweet_router
from app.routes.auth_routes import router as auth_router
from app.routes.agents_routes import router as agents_router


# Ensure logs from our `app.*` modules are visible.
# Uvicorn's default logging can filter out INFO logs from non-uvicorn loggers unless
# a handler/level is configured.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
logging.getLogger("app").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
# --- 1. CONFIGURATION & SETUP ---
# We initialize these globally so they are ready when the API starts
# HOST = os.getenv("HOST", "0.0.0.0")
HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", 8080))
BASE_URL = f"http://{HOST}:{PORT}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ui_agent, text_agent, agent_executor

    # base_url = "http://localhost:8080"   # adjust if you change host/port
    # ui_agent = RestaurantAgent(base_url=base_url, use_ui=True)
    # text_agent = RestaurantAgent(base_url=base_url, use_ui=False)
    # agent_executor = RestaurantAgentExecutor(ui_agent, text_agent)

    logger.info("Restaurant agents initialized successfully.")
    yield

    # Optional cleanup
    logger.info("Shutting down...")


app = FastAPI(lifespan=lifespan, title="Restaurant Agent Chat API")

# Static files live beside main.py (project root): social-be/images/
_IMAGES_DIR = Path(__file__).resolve().parent / "images"
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_IMAGES_DIR)), name="static")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. NEW API ENDPOINTS ---
@app.get("/health")
async def health_check():
    return {"status": "online", "agent": "RestaurantAgent"}

@app.get("/.well-known/agent-card.json")
async def get_info():
    """Triggered via standard GET to see the agent card."""
    return ui_agent.get_agent_card()


# Move these outside the endpoint — shared across requests
base_url = "http://localhost:8080"
ui_agent = RestaurantAgent(base_url=base_url, use_ui=True)
text_agent = RestaurantAgent(base_url=base_url, use_ui=False)
agent_executor = RestaurantAgentExecutor(ui_agent, text_agent)
task_store = InMemoryTaskStore()
request_handler = DefaultRequestHandler(
    agent_executor=agent_executor,
    task_store=task_store,
)

a2a_app = A2AStarletteApplication(
    agent_card=ui_agent.get_agent_card(),
    http_handler=request_handler,
)
a2a_app.add_routes_to_app(
    app,
    rpc_url="/chat",  # maps the A2A JSON-RPC handler to your /chat endpoint
)



# --- 5. RUNNING THE APP ---
if __name__ == "__main__":
    import uvicorn
    # Now we run the FastAPI 'app', not the 'main()' function
    uvicorn.run(app, host=HOST, port=PORT)