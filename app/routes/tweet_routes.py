from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import os
import sys
import json  # For pretty-printing JSON
from datetime import datetime, timezone, timedelta
import time
import asyncio
import ast

from app.models.tweet_model import Tweet, TweetCreate 
from app.services.twitter_services import send_tweet, fetch_home_timeline, generate_tweet
from app.services.tbot_services import send_message
from app.services.datastore_services import fetch_entities_by_property, find_similar_entities, get_cleaned_entities
from app.utils.helper import generate_oauth_params

router = APIRouter()

def fetch_n_twt():
    print(f"Start Time {time.time()}")
    try:
        # Fetch the timeline
        data = fetch_home_timeline()  # Assuming this returns a requests.Response object
        print("Retrived Timeline:", data)
        # data = r'''{ "data": [ { "id": "1993675402540437512", "author_id": "570122614", "edit_history_tweet_ids": ["1993675402540437512"], "created_at": "2025-11-26T13:37:11.000Z", "public_metrics": { "retweet_count": 393, "reply_count": 0, "like_count": 0, "quote_count": 0, "bookmark_count": 0, "impression_count": 0 }, "text": "RT @RajeevRC_X: 26/11: Pakistani terrorists killed 166 innocent Indians in Mumbai.\n\nAnd what was @INCIndia’s response?\n\nJustify the attack.…" }, { "id": "1993675132783698407", "author_id": "570122614", "edit_history_tweet_ids": ["1993675132783698407"], "created_at": "2025-11-26T13:36:07.000Z", "public_metrics": { "retweet_count": 42, "reply_count": 0, "like_count": 0, "quote_count": 0, "bookmark_count": 0, "impression_count": 0 }, "text": "RT @joedelhi: Let’s not exploit them and treat them with such cruelty.\n#NationalMilkDay #DairyDevelopment https://t.co/txCFhX3eCQ" } ], "includes": { "users": [ { "id": "570122614", "name": "Rahul", "username": "Creative_Unity" }, { "id": "797720168713441280", "name": "THE SKIN DOCTOR", "username": "theskindoctor13" }, { "id": "16362321", "name": "Chandra R. Srikanth", "username": "chandrarsrikant" }, { "id": "1391715496005824513", "name": "Indian Tech & Infra", "username": "IndianTechGuide" } ] }, "meta": { "next_token": "7140dibdnow9c7btw4e3foiaholc3to4e6w1fetlmopbr", "result_count": 10, "newest_id": "1993682179864342801", "oldest_id": "1993675132783698407" } }'''
        # data = json.loads(data)
        now = datetime.now(timezone.utc) # Get current time in UTC

        countt = 1
        relevant_entities = []
        # Traverse the 'data' array
        for tweet in data.get('data', []):
            print(f"Twt counter: {countt}")
            countt += 1
            created_at_str = tweet.get('created_at')
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))  # Parse ISO timestamp (remove .000Z if needed, but fromisoformat handles Z)
            
            # Check if less than 60 seconds ago
            time_diff = (now - created_at).total_seconds()
            if time_diff < 200:
                tw_text = tweet.get('text', '')
                matched_entities = find_similar_entities( kind="Published_Data_v1", input_text= tw_text, top_k = 2) ## Fetched entities with similarity > 0.5
                
                tweet_n_replies = []

                count = 1
                for m in matched_entities:
                    print(f"MatchedEntities counter: {count}")
                    print(f"{m['entity'].key.id_or_name} → {m['similarity']:.4f}")   
                    m_entity = m["entity"]
                    dataName = m_entity.get("dataName", "No dataName")
                    print(f"twtText:  {tw_text}  dataName  {dataName}")

                    entities = fetch_entities_by_property("TimeSeriesData", "dataName", dataName)
                    cleaned_data = get_cleaned_entities(entities)
                    print(f"CleanedData: {cleaned_data}")
                    relevant_entities.append({"entity_{count}": cleaned_data})
                    count += 1
        if len(relevant_entities) > 0:
            tw_reply_options = generate_tweet(relevant_entities, tw_text)  
            tweet_n_replies = f"TWT: {tw_text} \n RPLY_SUPPORT: {tw_reply_options['supportive']} \n RPLY_CONTRARIAN: {tw_reply_options['contrarian']}"            
            print(f"Msg for Tbot- {tweet_n_replies}")
            asyncio.run(send_message(tweet_n_replies));
            return {"msg": tweet_n_replies}
        else: 
            asyncio.run(send_message("No Relevant Tweet"));
            return {"msg": "No Relevant Tweet"}

            
    except Exception as e:
        print(f"Fetch_n_twt fun failed with error: {e}", file=sys.stderr)



@router.post("/send")
def send(tweet: TweetCreate):
    body = {"text": tweet.content}
    status, response = send_tweet(body)
    return {"status": status, "response": response}

@router.get("/home-timeline")
def send():
    status, response = fetch_home_timeline()
    return {"status": status, "response": response}

@router.get("/fetch-n-tweet")
def send():
    response = fetch_n_twt()
    return {"response": response}
