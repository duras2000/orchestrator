import os
import re
import json
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

    latest_msg = messages[0]
    snippet = latest_msg.get("snippet", "")
    email_headers = latest_msg.get("headers", {})

    # Step 2: Use GPT to extract meeting info
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

Here is the Email content:
{snippet}

Here are the email headers:
{email_headers}
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
        return {
            "email": snippet,
            "headers": email_headers,
            "extracted": reply
        }

    try:
        event_data = json.loads(reply)
    except Exception as e:
        return {
            "error": "Could not parse GPT reply",
            "raw": reply,
            "exception": str(e)
        }

    # Step 3: Resolve attendee names to emails (from headers)
    def resolve_name_to_email(name, header_fields):
        for field in header_fields:
            value = email_headers.get(field, "")
            matches = re.findall(r'([^<>,"]+)\s*<([^<>@]+@[^<>]+)>', value)
            for full_name, email in matches:
                if name.lower() in full_name.strip().lower():
                    return email
        return None

    resolved_attendees = []
    for name in event_data.get("attendees", []):
        email = resolve_name_to_email(name, ["to", "cc"])
        resolved_attendees.append(email if email else name)  # fallback to name

    event_data["attendees"] = resolved_attendees

    # Step 4: Schedule the event
    cal_resp = requests.post(
        f"{GCAL_WRAPPER_URL}/mcp/query",
        headers=headers,
        json={"tool": "create_event", "input": event_data}
    )

    return {
        "calendar_response": cal_resp.json(),
        "event_data": event_data
    }
