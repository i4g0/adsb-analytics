#!/usr/bin/env python3

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from openai import OpenAI
import os
from dotenv import load_dotenv

# --- Config ---
DB_PATH = Path.home() / "adsb-analytics" / "database" / "adsb_data.db"
SUMMARY_PATH = Path.home() / "adsb-analytics" / "summaries" / "today.txt"

load_dotenv()
client = OpenAI()  # This auto-loads your API key from env var OPENAI_API_KEY


def get_today_records() -> list[tuple]:
    """Query SQLite DB for today's aircraft data."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT hex, flight, lat, lon, alt_baro, track, speed
        FROM aircraft
        WHERE timestamp BETWEEN ? AND ?
    """, (start.isoformat(), end.isoformat()))

    return cursor.fetchall()


def build_summary_prompt(records: list[tuple]) -> str:
    if not records:
        return "No aircraft data was recorded today."

    lines = []
    for rec in records[:200]:  # Limit to prevent token overload
        hex_, flight, lat, lon, alt, track, speed = rec
        lines.append(f"{flight or 'N/A'} ({hex_}) at {alt or '?'} ft, {speed or '?'} kt, {lat}, {lon}")

    text_log = "\n".join(lines)

    prompt = f"""
You're an aviation analyst. Here's a log of today's detected aircraft over a Raspberry Pi ADS-B receiver near PDX airport.

Summarize notable traffic patterns, airlines, altitudes, police or military aviation activity, and anything interesting.

Please keep responses brief.

Log:
{text_log}
"""
    return prompt

def generate_summary(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.5,
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()

def write_summary(text: str) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        f.write(text)


def main():
    records = get_today_records()
    prompt = build_summary_prompt(records)
    summary = generate_summary(prompt)
    write_summary(summary)
    print(f"[✅] Summary written to {SUMMARY_PATH}")


if __name__ == "__main__":
    main()

