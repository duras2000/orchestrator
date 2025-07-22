import os
import re
import json
import requests
from datetime import datetime
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

    # Compute today's date (for GPT awareness)
    today = datetime.now().strftime("%Y-%m-%d")

    # Step 2: Use GPT-4 to extract meeting intent
    prompt = f"""
You are Lena, a highly competent virtual assistant based in Israel. You help Talmon manage his calendar by reading and interpreting email instructions.

Today’s date is {today}.
You are located in Israel. Please apply Israel Daylight Time (UTC+3) when converting local times like "12:30pm" into ISO 8601 timestamps.

Your tasks:
1. If Talmon asks you to set a meeting, extract the following:
   - A meaningful meeting title (even if not explicitly stated)
   - Start and end time in full ISO 8601 format (including timezone offset)
   - Attendee names (you will resolve them to email addresses using headers later)

2. Only suggest times between 10:00 AM and 4:00 PM Israel time unless Talmon explicitly says otherwise.

3. Use email headers (To, CC) to help determine who should be invited. You can also use the body of the email.

4. If you do not see someone's email in the headers or in the body of the message, do not make one up. They should not be invited.

Output a pure JSON block in the following format — no explanation, no markdown, just JSON:

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

    # Step 3: Parse GPT response safely
    try:
        event_data = json.loads(reply)
    except Exception as e:
        return {
            "error": "Could not parse GPT reply",
            "raw": reply,
            "exception": str(e)
        }

    # Step 4: Resolve attendee names to emails using headers
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
        if email and email.lower() != "talmon@gmail.com":
            resolved_attendees.append(email)
        elif name.lower() != "talmon":
            resolved_attendees.append(name)

    TALMON_EMAIL = "talmon@gmail.com"

    event_data["attendees"] = [
        {"email": TALMON_EMAIL, "responseStatus": "accepted"}
    ] + [
        {"email": a} for a in resolved_attendees
        if a.lower() != TALMON_EMAIL
    ]
    
    event_data["timezone"] = "Asia/Jerusalem"

    # Step 5: Create calendar event LXX
    print("EVENT DATA SENT TO CALENDAR WRAPPER:")
    print(json.dumps(event_data, indent=2))
    cal_resp = requests.post(
        f"{GCAL_WRAPPER_URL}/mcp/query",
        headers=headers,
        json={"tool": "create_event", "input": event_data}
    )
    try:
        cal_json = cal_resp.json()
    except Exception as e:
        cal_json = {
            "error": "Failed to decode calendar response as JSON",
            "status_code": cal_resp.status_code,
            "text": cal_resp.text,
            "exception": str(e)
        }

    return {
        "calendar_response": cal_json,
        "event_data": event_data
    }
    
