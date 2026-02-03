import os
import sys
import json  # For pretty-printing JSON
from datetime import datetime, timezone, timedelta
import time
import asyncio
import ast

# Add project root to path (your fix—good!)
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from app.services.twitter_services import fetch_home_timeline, generate_tweet
from app.services.datastore_services import find_similar_entities, fetch_entities_by_property
from app.services.tbot_services import send_message

def normalize_datastore_date(dt):
    """
    Takes a Datastore datetime field and returns a YYYY-MM-DD string in IST.
    Handles None, strings, and timezone-aware or naive datetime objects.
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if not dt:
        return "Unknown"

    # If dt is string, try to parse it
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return dt[:10]  # fallback

    if isinstance(dt, datetime):
        # If datetime is naive (no timezone), assume it's UTC from Datastore
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert UTC → IST
        dt_ist = dt.astimezone(IST)
        return dt_ist.strftime("%Y-%m-%d")

    # Anything else (unexpected type)
    return str(dt)[:10]

def get_cleaned_entities(entities: list[dict[str, any]]) -> str:
    # Group by item/dataName if multiple series; here assuming single series
    items = {}
    for entity in entities:
        item = entity.get('item', 'Unknown')
        if item not in items:
            items[item] = {'sector': entity.get('sector', 'Unknown'),
                           'subSector': entity.get('subSector', 'Unknown'),
                           'dataName': entity.get('dataName', item),
                           'units': entity.get('units', 'Unknown'),
                           'granularity': entity.get('granularity', 'Unknown'),
                           'series': []}
        
        # Format dateTime to YYYY-MM-DD
        dt = entity.get('dateTime')
        date_str = normalize_datastore_date(dt)
        series_entry = {
            'date': date_str,
            'value': entity.get('value', 0)
        }
        items[item]['series'].append(series_entry)
    
    # Sort series by date (assuming chronological)
    for item in items:
        items[item]['series'].sort(key=lambda x: x['date'])
    
    # Build formatted string
    cleaned_parts = []
    for item, data in items.items():
        header = f"{data['sector']} {data['subSector']} - {data['dataName']} ({data['units']}):"
        series_str = "\n".join([f"- {s['date']}: {s['value']}" for s in data['series']])
        cleaned_parts.append(f"{header}\n{series_str}")
    
    return "\n\n".join(cleaned_parts)

def run_job():
    print(f"Start Time {time.time()}")
    try:
        # Fetch the timeline
        # data = fetch_home_timeline()  # Assuming this returns a requests.Response object
        # print("TiMeLiNe:", timeline_response)
        data = r'''{ "data": [ { "id": "1993675402540437512", "author_id": "570122614", "edit_history_tweet_ids": ["1993675402540437512"], "created_at": "2025-11-26T13:37:11.000Z", "public_metrics": { "retweet_count": 393, "reply_count": 0, "like_count": 0, "quote_count": 0, "bookmark_count": 0, "impression_count": 0 }, "text": "RT @RajeevRC_X: 26/11: Pakistani terrorists killed 166 innocent Indians in Mumbai.\n\nAnd what was @INCIndia’s response?\n\nJustify the attack.…" }, { "id": "1993675132783698407", "author_id": "570122614", "edit_history_tweet_ids": ["1993675132783698407"], "created_at": "2025-11-26T13:36:07.000Z", "public_metrics": { "retweet_count": 42, "reply_count": 0, "like_count": 0, "quote_count": 0, "bookmark_count": 0, "impression_count": 0 }, "text": "RT @joedelhi: Let’s not exploit them and treat them with such cruelty.\n#NationalMilkDay #DairyDevelopment https://t.co/txCFhX3eCQ" } ], "includes": { "users": [ { "id": "570122614", "name": "Rahul", "username": "Creative_Unity" }, { "id": "797720168713441280", "name": "THE SKIN DOCTOR", "username": "theskindoctor13" }, { "id": "16362321", "name": "Chandra R. Srikanth", "username": "chandrarsrikant" }, { "id": "1391715496005824513", "name": "Indian Tech & Infra", "username": "IndianTechGuide" } ] }, "meta": { "next_token": "7140dibdnow9c7btw4e3foiaholc3to4e6w1fetlmopbr", "result_count": 10, "newest_id": "1993682179864342801", "oldest_id": "1993675132783698407" } }'''
        data = json.loads(data)
        now = datetime.now(timezone.utc) # Get current time in UTC

        countt = 1
        # Traverse the 'data' array
        for tweet in data.get('data', []):
            print(f"Twt counter: {countt}")
            countt += 1
            created_at_str = tweet.get('created_at')
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))  # Parse ISO timestamp (remove .000Z if needed, but fromisoformat handles Z)
            
            # Check if less than 60 seconds ago
            time_diff = (now - created_at).total_seconds()
            if time_diff > 60:
                tw_text = tweet.get('text', '')
                matched_entities = find_similar_entities( kind="Published_Data_v1", input_text= tw_text, top_k = 2) ## Fetched entities with similarity > 0.5
                relevant_entities = []
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
        else: 
            asyncio.run(send_message("No Relevant Tweet"));

            
    except Exception as e:
        print(f"Job failed with error: {e}", file=sys.stderr)



if __name__ == "__main__":
    run_job()