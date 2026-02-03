from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class Tweet(BaseModel):
    id: Optional[int] = None
    content: str = Field(..., min_length=1, max_length=280, description="Tweet content")
    author: str = Field(..., description="Author username")
    created_at: Optional[datetime] = None
    likes: int = 0
    retweets: int = 0

    class Config:
        json_schema_extra = {
            "example": {
                "content": "This is a sample tweet!",
                "author": "johndoe",
                "likes": 0,
                "retweets": 0
            }
        }

class TweetCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=280)
    author: str = Field(...)

class TweetUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=280)

