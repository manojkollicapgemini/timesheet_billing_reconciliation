import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.generative.engine.capgemini.com/v2/llm/invoke"
API_KEY = os.getenv("CAPG_LLM_API_KEY", "CHANGE_ME")
DEFAULT_MODEL = "mistral.mistral-7b-instruct-v0:2"

SYSTEM_PROMPT = (
    "You are a senior portfolio manager assistant for Capgemini working on Citi accounts. "
    "You see summarised timesheet, reconciliation and billing data. "
    "Always answer clearly, concisely, and in business language. "
    "If numbers are not in the provided context, say you don't know instead of guessing."
)


def call_llm(
    api_key: str,
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.3,
    top_p: float = 0.9,
    max_tokens: int = 512,
):
    if not api_key or api_key == "CHANGE_ME":
        raise RuntimeError(
            "CAPG_LLM_API_KEY is not set. Create a .env file with CAPG_LLM_API_KEY=..."
        )

    payload = {
        "action": "run",
        "modelInterface": "langchain",
        "data": {
            "mode": "chain",
            "text": prompt,
            "files": [],
            "modelName": model_name,
            "provider": "bedrock",
            "systemPrompt": system_prompt,
            "modelKwargs": {
                "maxTokens": int(max_tokens),
                "temperature": float(temperature),
                "streaming": False,
                "topP": float(top_p),
            },
        },
    }

    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }

    resp = requests.post(API_BASE, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()
