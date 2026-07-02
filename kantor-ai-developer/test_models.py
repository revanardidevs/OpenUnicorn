import os
from google.genai import Client
from dotenv import load_dotenv

load_dotenv()
try:
    client = Client(api_key=os.environ.get("GEMINI_API_KEY"))
    for model in client.models.list():
        print(model.name)
except Exception as e:
    print(f"Error: {e}")
