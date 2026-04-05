import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.utils.helper import generate_oauth_params
from fastapi import Response

router = APIRouter()



# @router.post("/chat")
# def chat():
#     json_response = { "id": 1, "jsonrpc": "2.0", "result": { "contextId": "47a5d8fa-8758-43ee-bec5-5a997135d637", "history": [ { "contextId": "47a5d8fa-8758-43ee-bec5-5a997135d637", "kind": "message", "messageId": "357217a8-ba4e-4932-af94-e2edaab57d90", "parts": [ { "kind": "text", "text": "Top 5 Chinese restaurants in New York." } ], "role": "user", "taskId": "1bb39ccb-ba9e-493a-b6da-caf0a1284681" }, { "contextId": "47a5d8fa-8758-43ee-bec5-5a997135d637", "kind": "message", "messageId": "fc609b34-43ce-4fc9-8afe-83c58510396a", "parts": [ { "kind": "text", "text": "Finding restaurants that match your criteria..." } ], "role": "agent", "taskId": "1bb39ccb-ba9e-493a-b6da-caf0a1284681" }, { "contextId": "47a5d8fa-8758-43ee-bec5-5a997135d637", "kind": "message", "messageId": "87c02e00-acfb-4992-a91e-f38faef9623d", "parts": [ { "kind": "text", "text": "Finding restaurants that match your criteria..." } ], "role": "agent", "taskId": "1bb39ccb-ba9e-493a-b6da-caf0a1284681" } ], "id": "1bb39ccb-ba9e-493a-b6da-caf0a1284681", "kind": "task", "status": { "message": { "contextId": "47a5d8fa-8758-43ee-bec5-5a997135d637", "kind": "message", "messageId": "b3dbac7c-c0c5-490f-862b-432b0d533c55", "parts": [ { "kind": "text", "text": "Here are the top 5 Chinese restaurants in New York:" }, { "data": { "beginRendering": { "surfaceId": "default", "root": "root-column", "styles": { "primaryColor": "#FF0000", "font": "Roboto" } } }, "kind": "data", "metadata": { "mimeType": "application/json+a2ui" } }, { "data": { "surfaceUpdate": { "surfaceId": "default", "components": [ { "id": "root-column", "component": { "Column": { "children": { "explicitList": [ "title-heading", "item-list" ] } } } }, { "id": "title-heading", "component": { "Text": { "usageHint": "h1", "text": { "path": "/title" } } } }, { "id": "item-list", "component": { "List": { "direction": "vertical", "children": { "template": { "componentId": "item-card-template", "dataBinding": "/items" } } } } }, { "id": "item-card-template", "component": { "Card": { "child": "card-layout" } } }, { "id": "card-layout", "component": { "Row": { "children": { "explicitList": [ "template-image", "card-details" ] } } } }, { "id": "template-image", "weight": 1, "component": { "Image": { "url": { "path": "/imageUrl" } } } }, { "id": "card-details", "weight": 2, "component": { "Column": { "children": { "explicitList": [ "template-name", "template-rating", "template-detail", "template-link", "template-book-button" ] } } } }, { "id": "template-name", "component": { "Text": { "usageHint": "h3", "text": { "path": "/name" } } } }, { "id": "template-rating", "component": { "Text": { "text": { "path": "/rating" } } } }, { "id": "template-detail", "component": { "Text": { "text": { "path": "/detail" } } } }, { "id": "template-link", "component": { "Text": { "text": { "path": "/infoLink" } } } }, { "id": "template-book-button", "component": { "Button": { "child": "book-now-text", "primary": True, "action": { "name": "book_restaurant", "context": [ { "key": "restaurantName", "value": { "path": "/name" } }, { "key": "imageUrl", "value": { "path": "/imageUrl" } }, { "key": "address", "value": { "path": "/address" } } ] } } } }, { "id": "book-now-text", "component": { "Text": { "text": { "literalString": "Book Now" } } } } ] } }, "kind": "data", "metadata": { "mimeType": "application/json+a2ui" } }, { "data": { "dataModelUpdate": { "surfaceId": "default", "path": "/", "contents": [ { "key": "title", "valueString": "Top 5 Chinese Restaurants in New York" }, { "key": "items", "valueMap": [ { "key": "item1", "valueMap": [ { "key": "name", "valueString": "Xi'an Famous Foods" }, { "key": "rating", "valueString": "★★★★☆" }, { "key": "detail", "valueString": "Spicy and savory hand-pulled noodles." }, { "key": "infoLink", "valueString": "[More Info](https://www.xianfoods.com/)" }, { "key": "imageUrl", "valueString": "http://localhost:10002/static/shrimpchowmein.jpeg" }, { "key": "address", "valueString": "81 St Marks Pl, New York, NY 10003" } ] }, { "key": "item2", "valueMap": [ { "key": "name", "valueString": "Han Dynasty" }, { "key": "rating", "valueString": "★★★★☆" }, { "key": "detail", "valueString": "Authentic Szechuan cuisine." }, { "key": "infoLink", "valueString": "[More Info](https://www.handynasty.net/)" }, { "key": "imageUrl", "valueString": "http://localhost:10002/static/mapotofu.jpeg" }, { "key": "address", "valueString": "90 3rd Ave, New York, NY 10003" } ] }, { "key": "item3", "valueMap": [ { "key": "name", "valueString": "RedFarm" }, { "key": "rating", "valueString": "★★★★☆" }, { "key": "detail", "valueString": "Modern Chinese with a farm-to-table approach." }, { "key": "infoLink", "valueString": "[More Info](https://www.redfarmnyc.com/)" }, { "key": "imageUrl", "valueString": "http://localhost:10002/static/beefbroccoli.jpeg" }, { "key": "address", "valueString": "529 Hudson St, New York, NY 10014" } ] }, { "key": "item4", "valueMap": [ { "key": "name", "valueString": "Mott 32" }, { "key": "rating", "valueString": "★★★★★" }, { "key": "detail", "valueString": "Upscale Cantonese dining." }, { "key": "infoLink", "valueString": "[More Info](https://mott32.com/newyork/)" }, { "key": "imageUrl", "valueString": "http://localhost:10002/static/springrolls.jpeg" }, { "key": "address", "valueString": "111 W 57th St, New York, NY 10019" } ] }, { "key": "item5", "valueMap": [ { "key": "name", "valueString": "Hwa Yuan Szechuan" }, { "key": "rating", "valueString": "★★★★☆" }, { "key": "detail", "valueString": "Famous for its cold noodles with sesame sauce." }, { "key": "infoLink", "valueString": "[More Info](https://hwayuannyc.com/)" }, { "key": "imageUrl", "valueString": "http://localhost:10002/static/kungpao.jpeg" }, { "key": "address", "valueString": "40 E Broadway, New York, NY 10002" } ] } ] } ] } }, "kind": "data", "metadata": { "mimeType": "application/json+a2ui" } } ], "role": "agent", "taskId": "1bb39ccb-ba9e-493a-b6da-caf0a1284681" }, "state": "input-required", "timestamp": "2026-03-14T09:38:25.050100+00:00" } } }
#     return  json_response




class AgentCapabilities:
    def __init__(self, allow_files: bool = False):
        self.allow_files = allow_files

    def dict(self):
        return {"allow_files": self.allow_files}

class AgentManifest:
    def __init__(
        self,
        name: str,
        description: str,
        chat_url: str,
        version: str,
        capabilities: AgentCapabilities,
        manifest_version: str
    ):
        self.name = name
        self.description = description
        self.chat_url = chat_url
        self.version = version
        self.capabilities = capabilities
        self.manifest_version = manifest_version

    def dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "chat_url": self.chat_url,
            "version": self.version,
            "capabilities": self.capabilities.dict(),
            "manifest_version": self.manifest_version
        }

# @router.get("/.well-known/agent-card.json")
# def get_agent_card():
#     agent_manifest = AgentManifest(
#         name="JavaLLMAgent",
#         description="Simple LLM agent via A2A",
#         chat_url="http://127.0.0.1:8000/agents/chat",
#         version="1.0.0",
#         capabilities=AgentCapabilities(allow_files=False),
#         manifest_version="0.3.0"
#     )
#     return agent_manifest.dict()