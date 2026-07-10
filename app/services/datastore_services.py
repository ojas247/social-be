from google.cloud import datastore
import numpy as np
from datetime import datetime, timezone, timedelta
from app.utils.models import generate_embedding
from google.cloud.datastore.query import PropertyFilter
from typing import List, Dict, Any, Optional
from app.utils.dates import get_last_date_of_month

client = datastore.Client()

def fetch_entity(kind, id_or_name):
    key = client.key(kind, id_or_name)
    entity = client.get(key)
    return entity



def fetch_entities_by_property(
        kind: str,
        property_name: str,
        value: Any,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
    """
        Generic function to fetch entities from a Datastore kind filtered by a property value.
    
        Args:
            client: Initialized Datastore client.
            kind: The Datastore kind (e.g., 'Report').
            property_name: The property to filter on (e.g., 'dataName').
            value: The value to match (exact equality filter).
            limit: Optional limit on number of results (default: None, fetches all).
        
        Returns:
            List of entities as dicts (each entity's key and properties).
    """

    query = client.query(kind=kind)
    # query.add_filter(property_name,"=",value)    
    query.add_filter(filter=PropertyFilter(property_name, "=", value))  
    # if limit:
    #     query.limit(limit)
    
    #return list(query.fetch())
    return list(query.fetch(limit=limit))


def update_entity(properties: dict, kind: str = "TimeSeriesData") -> Dict[str, Any]:
    """Create or write a Datastore entity with the given properties (auto-generated key)."""
    key = client.key(kind)
    entity = datastore.Entity(key=key)
    entity.update(properties)
    client.put(entity)
    print(f"Entity updated: {entity.key.id_or_name}")
    return dict(entity)

# ======  Embed Text ======
def embed_text(text: str):
    return np.array(generate_embedding(text))


# ======  Cosine similarity ======
def cosine_similarity(v1, v2):
    v1 = np.array(v1)
    v2 = np.array(v2)
    if np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
        return 0.0
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def find_similar_entities(kind: str, input_text: str, top_k: int = 10):
    """
    Given input text, return top_k similar entities of a Datastore kind.
    Each entity must already have an 'embedding' property.
    """

    print("Embedding query text...")
    query_embedding = embed_text(input_text)

    query = client.query(kind=kind)
    results = query.fetch()

    scored_entities = []

    for entity in results:
        if "embedding" not in entity:
            continue  # skip unprocessed entities

        score = cosine_similarity(query_embedding, entity["embedding"])
        print(f"Socre of {input_text} against Entity {entity['dataName']} --> {score}")
        if score > 0.3:
            scored_entities.append({
                "entity": entity,
                "similarity": score
            })

    # Sort by similarity DESC
    scored_entities.sort(key=lambda x: x["similarity"], reverse=True)

    return scored_entities[:top_k]


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


def update_Datastore(parsed: dict, date: str, dataName: str, kind: str = "StagingData_v1" ):
    """
    Update the Datastore entity with the parsed data.
    """
    for item, value in parsed.items():
        entity = {
            "item": item,
            "value": value,
            "dataName": dataName,
            "publishedTS": datetime.now(), 
            "dateTime": get_last_date_of_month(date),
            "granularity": "Cumulative"

        }
        update_entity(entity, kind="StagingData_v1")





