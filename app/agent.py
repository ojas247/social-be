import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent



# load environment variables
load_dotenv()


root_agent = LlmAgent(
    name = "Social_Media_Assistant",
    model = "gemini-2.5-flash",
    description="Helps creating engaging social media content",
    instruction=(
        "You are a creative social media asssistant"
        "Help users with some awesome content"
    ),
    tools=[],
)