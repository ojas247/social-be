1) Activate venv -> venv_x\Scripts\Activate.ps1
2) Run Uviconr -> uvicorn main:app --reload --host 127.0.0.1 --port 8000
3) Run ADK Agent -> (venv_x) PS C:\Users\Ojas\Tech\Projects\social\social-be> adk api_server
4) Run ADK Agent handling cors -> adk api_server --allow_origins "regex:http://localhost:\d+" .
