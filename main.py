from fastapi import FastAPI
from app.routes.tweet_routes import router as tweet_router
from app.routes.auth_routes import router as auth_router

app = FastAPI(
    title="Twitter Clone API",
    version="1.0",
    description="A simple FastAPI starter app for learning."
)

# Include routes
app.include_router(tweet_router, prefix="/tweets", tags=["Tweets"])
app.include_router(auth_router, tags=["Auth"])


from google.cloud import datastore
client = datastore.Client()

def fetch_entity(kind, id_or_name):
    key = client.key(kind, id_or_name)
    entity = client.get(key)
    return entity


@app.get("/")
def read_root():
    result = fetch_entity("Crawler_Dashboard", "https://irdai.gov.in/handbook-of-indian-insurance")
    print(result)
    return {"message": result}
