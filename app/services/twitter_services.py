import os
import re
import json
import requests
import google.generativeai as genai
# from google.generativeai import GenerativeModel
from app.utils.config import settings
from app.utils.helper import (
    generate_oauth_params,
    generate_oauth_signature,
    build_oauth_header
)

genai.configure(api_key= settings.LLM_API_KEY)
# model = GenerativeModel("models/gemini-1.5-flash")
model = genai.GenerativeModel("models/gemini-2.0-flash")


def send_tweet(body: dict):
    # request_url = os.getenv("REQUEST_URL")
    request_url = settings.REQUEST_URL

    oauth_params = generate_oauth_params()

    # Generate signature
    signature = generate_oauth_signature(
        request_url=request_url,
        request_method="POST",
        oauth_params=oauth_params
    )

    # Build header
    oauth_header = build_oauth_header(oauth_params, signature)

    headers = {
        "Authorization": oauth_header,
        "Content-Type": "application/json"
    }

    response = requests.post(
        request_url,
        headers=headers,
        data=json.dumps(body)
    )

    return response.status_code, response.text


def fetch_home_timeline():
    """
    Fetch the authenticated user's Home Timeline from Twitter API v2.
    """

    user_id = settings.XUSERID   # Your Twitter user ID

    request_url = (
        f"https://api.twitter.com/2/users/{user_id}/timelines/reverse_chronological"
        "?max_results=10"
        "&tweet.fields=created_at,public_metrics,author_id"
        "&expansions=author_id"
        "&user.fields=name,username"
    )

    # Step 1: Generate OAuth parameters
    oauth_params = generate_oauth_params()

    # Step 2: Generate OAuth 1.0a signature
    signature = generate_oauth_signature(request_url, "GET", oauth_params)

    # Step 3: Build Authorization header
    oauth_header = build_oauth_header(oauth_params, signature)

    headers = {
        "Authorization": oauth_header,
        "Content-Type": "application/json"
    }

    # Step 4: Make GET request
    response = requests.get(request_url, headers=headers)

    print("Status Code:", response.status_code)

    # Pretty print JSON
    try:
        print("Home Timeline Response:")
        print(response.json())
        return response.json()
    except Exception:
        return response.status_code, response


def generate_tweet(relevant_entities, original_tweet):
    """
    Generates two reply tweets (supportive + contrarian)
    using an entity's properties and the original tweet text.
    """

    prompt = f"""
        You are helping write two tweet replies.

        Original Tweet:
        {original_tweet}

        Here is structured factual data from my datastore entity:
        {relevant_entities}

        Using ONLY this context, generate:

        **Option 1 (Supportive View)**:
        A short, insightful reply tweet agreeing with or expanding the original thought. 
        Max 220 characters. Must reference the data in a meaningful way.

        **Option 2 (Contrarian View)**:
        A short, sharp reply offering a respectful contrarian take.
        Max 220 characters. Must also use the data meaningfully.

        Write the output in this JSON format:

        {{
        "supportive": "...",
        "contrarian": "..."
        }}
        DO NOT add commentary. DO NOT add markdown. ONLY JSON.
        """

    response = model.generate_content(prompt)

     # Convert Gemini response to dict
    data = response.to_dict()
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

    # 1️⃣ Extract JSON even if wrapped in ```json ... ```
    json_match = re.search(r"{.*}", text, re.DOTALL)
    if json_match:
        json_str = json_match.group(0)
    else:
        raise ValueError(f"Gemini did not return JSON. Returned text:\n{text}")

    # 2️⃣ Try parsing
    try:
        return json.loads(json_str)
    except Exception as e:
        raise ValueError(f"Invalid JSON returned:\n{json_str}\nError: {e}")
