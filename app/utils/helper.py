import time
import uuid
from app.utils.config import settings
import base64
import hashlib
import hmac
import urllib.parse

def generate_nonce():
    return uuid.uuid4().hex

def generate_oauth_params():
    params = {
        "oauth_consumer_key": settings.CONSUMER_KEY,
        "oauth_token": settings.ACCESS_TOKEN,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": generate_nonce(),
        "oauth_version": "1.0"
    }
    return params


def generate_oauth_signature(request_url, request_method, oauth_params):
    """
    Generate OAuth 1.0a HMAC-SHA1 Signature
    """

    # --- Parse query parameters from URL ---
    parsed_url = urllib.parse.urlparse(request_url)
    query_params = dict(urllib.parse.parse_qsl(parsed_url.query))

    # Merge OAuth params + query params
    all_params = {**oauth_params, **query_params}

    # --- Sort parameters alphabetically ---
    sorted_keys = sorted(all_params.keys())

    # --- Build parameter string ---
    param_list = []
    for key in sorted_keys:
        param_list.append(f"{key}={urllib.parse.quote(all_params[key], safe='')}")
    parameter_string = "&".join(param_list)

    # --- Create Base URL (without query params) ---
    clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

    # --- Create Base String ---
    base_elems = [
        request_method.upper(),
        urllib.parse.quote(clean_url, safe=""),
        urllib.parse.quote(parameter_string, safe="")
    ]
    base_string = "&".join(base_elems)

    print("DEBUG - Base String:", base_string)

    # --- Create Signing Key ---
    signing_key = (
        urllib.parse.quote(settings.CONSUMER_SECRET, safe="") +
        "&" +
        urllib.parse.quote(settings.ACCESS_TOKEN_SECRET, safe="")
    )

    print("DEBUG - Signing Key (redacted):", signing_key[:10] + "...")

    # --- HMAC-SHA1 signature ---
    hashed = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1
    )
    signature = base64.b64encode(hashed.digest()).decode()

    return signature


def build_oauth_header(oauth_params, signature):
    """
    Build OAuth 1.0 Authorization Header
    """

    def enc(v):
        return urllib.parse.quote(v, safe="")

    header_params = [
        f'oauth_consumer_key="{enc(oauth_params["oauth_consumer_key"])}"',
        f'oauth_token="{enc(oauth_params["oauth_token"])}"',
        f'oauth_signature_method="{enc(oauth_params["oauth_signature_method"])}"',
        f'oauth_timestamp="{enc(oauth_params["oauth_timestamp"])}"',
        f'oauth_nonce="{enc(oauth_params["oauth_nonce"])}"',
        f'oauth_version="{enc(oauth_params["oauth_version"])}"',
        f'oauth_signature="{enc(signature)}"'
    ]

    return "OAuth " + ", ".join(header_params)
