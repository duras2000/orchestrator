
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
    prompt = f"""
You are Lena, a friendly yet professional virtual assistant. You help Talmon by managing his calendar based on email instructions.

Your tasks:
1. If Talmon asks you to set a meeting, extract the following:
   - Meeting title (use your judgment if not explicitly stated)
   - Start and end time (including timezone)
   - List of attendees (use email headers like To/CC and names mentioned in the message)

2. Only suggest times between 10:00 AM and 4:00 PM local time unless Talmon specifically asks for a different time.

3. Talmon might tell you his current location or schedule in the message — use that to adjust for local time.

4. If someone else suggests a meeting time outside those hours, do not confirm it — simply extract the intent, and let Talmon decide.

5. If Talmon explicitly requests a specific time, always follow it, even if it's outside normal working hours.

6. Look at email headers (from, to, cc) to help determine who should be invited.

Output your response as a JSON block with the following format (without explanation):

{{
  "summary": "...",
  "start": "...",
  "end": "...",
  "attendees": ["..."]
}}

Email content:
{latest}
"""
    chat = client.chat.completions.create(
        model="gpt-4o",
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
