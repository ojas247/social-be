from fastapi import APIRouter
from app.utils.helper import generate_oauth_params

router = APIRouter()

@router.get("/auth")
def test_auth():
    return generate_oauth_params()

