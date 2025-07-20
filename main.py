
import os
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI()

# Load env vars
GMAIL_WRAPPER_URL = os.environ["GMAIL_WRAPPER_URL"]
GCAL_WRAPPER_URL = os.environ["GCAL_WRAPPER_URL"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
BEARER_TOKEN = os.environ["MCP_BEARER_TOKEN"]

client = OpenAI(api_key=OPENAI_API_KEY)

class OrchestratorInput(BaseModel):
    dry_run: bool = False

@app.post("/run")
def orchestrate(input: OrchestratorInput):
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json"
    }

    # Step 1: Read emails
    gmail_resp = requests.post(
        f"{GMAIL_WRAPPER_URL}/mcp/query",
        headers=headers,
        json={"tool": "read_unread_emails", "input": {}}
    )
    messages = gmail_resp.json().get("messages", [])

    if not messages:
        return {"status": "No recent messages found."}

    latest = messages[0]["snippet"]

    # Step 2: Use GPT-4 to check if it's a scheduling request
    prompt = f"You are a virtual assistant. Extract from this email whether the user is trying to schedule a meeting. If yes, return JSON like: {{'summary': '...', 'start': '...', 'end': '...', 'attendees': ['...']}}. Email:\n\n{latest}"

    chat = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You help extract calendar events from emails."},
            {"role": "user", "content": prompt}
        ]
    )
    reply = chat.choices[0].message.content.strip()

    if input.dry_run:
        return {"email": latest, "extracted": reply}

    try:
        event_data = eval(reply)
    except:
        return {"error": "Could not parse GPT reply", "raw": reply}

    # Step 3: Schedule it
    cal_resp = requests.post(
        f"{GCAL_WRAPPER_URL}/mcp/query",
        headers=headers,
        json={"tool": "create_event", "input": event_data}
    )
    return {"calendar_response": cal_resp.json()}
