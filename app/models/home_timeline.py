from app.utils.helper import generate_oauth_params


def fetch_home_timeline():
    """
    Fetch the home timeline.
    """
    user_id = "1650713265994092544"  # Hardcoded user ID

    request_url = f"https://api.twitter.com/2/users/{user_id}/timelines/reverse_chronological?max_results=10&tweet.fields=created_at,public_metrics,author_id&expansions=author_id&user.fields=name,username"

    oauth_params = generate_oauth_params()
    signature = generate_oauth_signature(request_url, "GET", oauth_params)
    oauth_header = build_oauth_header(oauth_params, signature)

    req = Request(request_url, method='GET')
    req.add_header('Authorization', oauth_header)

    try:
        with urlopen(req) as response:
            response_code = response.getcode()
            print(f"Home Timeline Response code: {response_code}")
            
            response_data = response.read().decode('utf-8')
            print(f"Home Timeline: {response_data}")
    except HTTPError as e:
        print(f"Error: {e.code} - {e.read().decode('utf-8')}", file=sys.stderr)