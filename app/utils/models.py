# import time
# import base64
# import hashlib
# import hmac
# import urllib.parse
from sentence_transformers import SentenceTransformer



def generate_embedding(text: str):
    T_model = SentenceTransformer("all-MiniLM-L6-v2")
    if not text:
        return None
    return T_model.encode(text).tolist()


