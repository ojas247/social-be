import os
import sys
from google.cloud import datastore

# Add project root to path (your fix—good!)
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from app.utils.models import generate_embedding


# Load once globally
# model = SentenceTransformer("all-MiniLM-L6-v2")
client = datastore.Client()

# def generate_embedding(text: str):
#     if not text:
#         return None
#     return model.encode(text).tolist()

def generate_search_text(entity):
    """
    Convert entity fields into a clean, descriptive sentence for embeddings.
    Automatically handles missing fields.
    """

    data_name = entity.get("dataName", "")
    dataDesc = entity.get("dataDesc", "")
    sector = entity.get("sector", "")
    sub1 = entity.get("sub1", "")
    units = entity.get("units", "")
    gran = entity.get("granularity", "")

    parts = []

    if data_name:
        parts.append(f"Title of this dataset is {data_name}.")

    if dataDesc:
        parts.append(f"This is a brief description of about the data {gran}.")

    if sector:
        sec_text = f"This dataset belongs to the {sector} sector"
        if sub1:
            sec_text += f", further classified into {sub1} sub-sector"
        sec_text += "."
        parts.append(sec_text)

    # if units or gran:
    #     parts.append(
    #         f"Values are recorded in {units if units else 'unknown units'} "
    #         f"with {gran if gran else 'unknown'} frequency."
    #     )

    return " ".join(parts)



def save_embedding_for_entity(entity_key):
    print(f"EntityKey: {entity_key}")
    entity = client.get(entity_key)

    # Already has embedding? Skip.
    if "embedding" in entity:
        print(f"Skipping {entity_key.id_or_name}: embedding already exists")
        return False

    # Build text input
    search_text = generate_search_text(entity)

    # Generate embedding
    embedding = generate_embedding(search_text)

    # Update properties with exclude_from_indexes=True
    # entity["search_text"] = search_text
    # entity.exclude_from_indexes.add("search_text")
    print(f"this is search -- {search_text}")

    entity["embedding"] = embedding
    entity.exclude_from_indexes.add("embedding")
    client.put(entity)

    print(f"Updated entity {entity_key.id_or_name} with embedding.")
    return True


def update_missing_embeddings(kind, batch_size=500):
    """
    Efficiently scans Datastore entities and updates only missing embeddings.
    Uses cursor-based pagination to avoid loading all entities into memory.
    """
    query = client.query(kind=kind)
    cursor = None
    updated_count = 0
    scanned_count = 0

    while True:
        # Fetch batch
        iterator = query.fetch(start_cursor=cursor, limit=batch_size)
        page = next(iterator.pages, None)

        if page is None:
            break  # No more results

        for entity in page:
            scanned_count += 1

            if "embedding" not in entity:
                save_embedding_for_entity(entity.key)
                updated_count += 1

                if updated_count % 500 == 0:
                    print(f"Updated {updated_count} entities...")

        cursor = iterator.next_page_token
        if cursor is None:
            break

    print(f"✓ Scan complete.")
    print(f"Total scanned: {scanned_count}")
    print(f"Embeddings added: {updated_count}")


if __name__ == "__main__":
    print(f"started")
    # update_missing_embeddings("TimeSeriesData")
    update_missing_embeddings("Published_Data_v1")
