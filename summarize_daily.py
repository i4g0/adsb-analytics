#!/usr/bin/env python3

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from openai import OpenAI
import os
from dotenv import load_dotenv
from collections import Counter

# --- Config ---
DB_PATH = Path.home() / "adsb-analytics" / "database" / "adsb_data.db"
SUMMARY_PATH = Path.home() / "adsb-analytics" / "summaries" / "today.txt"

load_dotenv()
client = OpenAI()  # Auto-loads OPENAI_API_KEY from env


def get_today_records() -> dict:
    """Query SQLite DB for today's aircraft data with enrichment."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get unique aircraft with enriched data
    cursor.execute("""
    SELECT DISTINCT
        a.hex,
        COALESCE(e.registration, a.flight, 'Unknown') as identity,
        e.type,
        e.manufacturer,
        e.operator,
        e.origin_country,
        MAX(a.alt_baro) as max_altitude,
        MIN(a.alt_baro) as min_altitude,
        AVG(a.speed) as avg_speed,
        MIN(a.lat) as min_lat,
        MAX(a.lat) as max_lat,
        MIN(a.lon) as min_lon,
        MAX(a.lon) as max_lon,
        COUNT(*) as ping_count
    FROM aircraft a
    LEFT JOIN aircraft_enriched e ON a.hex = e.hex
    WHERE a.timestamp >= ? AND a.timestamp < ?
    GROUP BY a.hex
    ORDER BY ping_count DESC
""", (start.isoformat(), end.isoformat()))
    
    aircraft_data = cursor.fetchall()
    
    # Get interesting statistics
    cursor.execute("""
        SELECT 
            COUNT(DISTINCT e.hex) as total_aircraft,
            COUNT(DISTINCT CASE WHEN lat IS NOT NULL THEN e.hex END) as with_position,
            COUNT(DISTINCT e.hex) as enriched_count,
            MAX(alt_baro) as highest_altitude,
            COUNT(DISTINCT CASE WHEN speed > 500 THEN e.hex END) as high_speed_count
        FROM aircraft a
        LEFT JOIN aircraft_enriched e ON a.hex = e.hex
        WHERE a.timestamp >= ? AND a.timestamp < ?
    """, (start.isoformat(), end.isoformat()))
    
    stats = cursor.fetchone()
    
    # Get operator statistics
    cursor.execute("""
        SELECT 
            e.operator,
            COUNT(DISTINCT a.hex) as aircraft_count
        FROM aircraft a
        JOIN aircraft_enriched e ON a.hex = e.hex
        WHERE a.timestamp >= ? AND a.timestamp < ?
          AND e.operator IS NOT NULL
        GROUP BY e.operator
        ORDER BY aircraft_count DESC
        LIMIT 10
    """, (start.isoformat(), end.isoformat()))
    
    top_operators = cursor.fetchall()
    
    # Get aircraft type distribution
    cursor.execute("""
        SELECT 
            e.type,
            COUNT(DISTINCT a.hex) as count
        FROM aircraft a
        JOIN aircraft_enriched e ON a.hex = e.hex
        WHERE a.timestamp >= ? AND a.timestamp < ?
          AND e.type IS NOT NULL
        GROUP BY e.type
        ORDER BY count DESC
        LIMIT 10
    """, (start.isoformat(), end.isoformat()))
    
    aircraft_types = cursor.fetchall()
    
    conn.close()
    
    return {
        'aircraft': aircraft_data,
        'stats': stats,
        'top_operators': top_operators,
        'aircraft_types': aircraft_types
    }


def find_interesting_aircraft(aircraft_list):
    """Identify potentially interesting aircraft."""
    interesting = {
        'military': [],
        'police': [],
        'medical': [],
        'high_altitude': [],
        'low_altitude': [],
        'international': [],
        'private_jets': [],
        'unusual': []
    }
    
    for ac in aircraft_list:
        hex_code, identity, ac_type, manufacturer, operator, country, max_alt, min_alt, avg_speed, *rest = ac
        squawk = rest[-2] if len(rest) >= 2 else None
        
        # Military/Government
        if operator and any(term in operator.lower() for term in ['military', 'air force', 'navy', 'army', 'guard']):
            interesting['military'].append((identity, operator, ac_type))
        
        # Police/Law Enforcement
        if operator and any(term in operator.lower() for term in ['police', 'sheriff', 'patrol']):
            interesting['police'].append((identity, operator, ac_type))
        
        # Medical/Emergency
        if operator and any(term in operator.lower() for term in ['medical', 'life flight', 'ambulance', 'hospital']):
            interesting['medical'].append((identity, operator, ac_type))
        
        # High altitude (>40,000 ft)
        if max_alt and max_alt > 40000:
            interesting['high_altitude'].append((identity, max_alt, ac_type))
        
        # Very low altitude (<1,000 ft) - possible local traffic
        if min_alt and min_alt < 1000 and avg_speed and avg_speed > 50:
            interesting['low_altitude'].append((identity, min_alt, ac_type))
        
        # International (non-US registered)
        if identity and not identity.startswith('N') and len(identity) > 3:
            interesting['international'].append((identity, country, operator))
        
        # Private jets
        if ac_type and any(jet in ac_type.upper() for jet in ['GLF', 'CL60', 'C750', 'FA50', 'E550']):
            interesting['private_jets'].append((identity, ac_type, operator))
        
        # Emergency squawks
        if squawk and squawk in ['7700', '7600', '7500']:
            interesting['unusual'].append((identity, f"EMERGENCY SQUAWK {squawk}", ac_type))
    
    return interesting


def build_summary_prompt(data: dict) -> str:
    """Build a comprehensive prompt for GPT."""
    stats = data['stats']
    aircraft = data['aircraft']
    
    if not aircraft:
        return "No aircraft data was recorded today."
    
    # Basic statistics
    total, with_pos, enriched, highest, high_speed = stats
    
    # Find interesting aircraft
    interesting = find_interesting_aircraft(aircraft)
    
    # Build prompt
    prompt = f"""You're an aviation analyst providing a daily summary for an ADS-B receiver near PDX airport.

TODAY'S STATISTICS:
- Total unique aircraft: {total}
- Aircraft with position data: {with_pos}
- Aircraft with enriched data: {enriched}
- Highest altitude observed: {highest:,} ft
- High-speed aircraft (>500 kt): {high_speed}

TOP 10 OPERATORS:
"""
    
    for op, count in data['top_operators']:
        prompt += f"- {op}: {count} aircraft\n"
    
    prompt += "\nAIRCRAFT TYPES (Top 10):\n"
    for ac_type, count in data['aircraft_types']:
        prompt += f"- {ac_type}: {count}\n"
    
    # Add interesting aircraft sections
    if interesting['military']:
        prompt += f"\nMILITARY AIRCRAFT ({len(interesting['military'])}):\n"
        for identity, op, ac_type in interesting['military'][:5]:
            prompt += f"- {identity} ({ac_type}) - {op}\n"
    
    if interesting['police']:
        prompt += f"\nLAW ENFORCEMENT ({len(interesting['police'])}):\n"
        for identity, op, ac_type in interesting['police'][:5]:
            prompt += f"- {identity} ({ac_type}) - {op}\n"
    
    if interesting['medical']:
        prompt += f"\nMEDICAL/EMERGENCY ({len(interesting['medical'])}):\n"
        for identity, op, ac_type in interesting['medical'][:5]:
            prompt += f"- {identity} ({ac_type}) - {op}\n"
    
    if interesting['high_altitude']:
        prompt += f"\nHIGH ALTITUDE (>40,000 ft):\n"
        for identity, alt, ac_type in interesting['high_altitude'][:5]:
            prompt += f"- {identity} at {alt:,} ft ({ac_type})\n"
    
    if interesting['private_jets']:
        prompt += f"\nPRIVATE JETS ({len(interesting['private_jets'])}):\n"
        for identity, ac_type, op in interesting['private_jets'][:5]:
            prompt += f"- {identity} ({ac_type}) - {op or 'Unknown operator'}\n"
    
    if interesting['unusual']:
        prompt += f"\nUNUSUAL/EMERGENCY:\n"
        for identity, issue, ac_type in interesting['unusual']:
            prompt += f"- {identity} ({ac_type}) - {issue}\n"
    
    prompt += """
Please provide a natural language summary that:
1. Highlights patterns in airline traffic (which airlines dominated)
2. Notes any military, police, or emergency aircraft activity
3. Mentions interesting international traffic
4. Comments on general aviation activity (private jets, small aircraft)
5. Provides insights about traffic patterns throughout the day
6. Notes anything unusual or noteworthy

Keep the summary engaging and informative, about 3-4 paragraphs.
"""
    
    return prompt


def generate_summary(prompt: str) -> str:
    """Generate summary using OpenAI."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an aviation expert providing daily traffic summaries. Be informative but conversational."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=800,
    )
    return response.choices[0].message.content.strip()


def write_summary(text: str) -> None:
    """Write summary with metadata."""
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Add header with timestamp
    header = f"ADS-B Daily Summary - {datetime.now().strftime('%A, %B %d, %Y')}\n"
    header += "=" * len(header) + "\n\n"
    
    with open(SUMMARY_PATH, "w") as f:
        f.write(header + text)
    
    # Also save with date in filename for history
    dated_path = SUMMARY_PATH.parent / f"summary_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(dated_path, "w") as f:
        f.write(header + text)


def main():
    print("[INFO] Fetching today's aircraft data...")
    data = get_today_records()
    
    if not data['aircraft']:
        print("[WARN] No aircraft data found for today")
        write_summary("No aircraft data was recorded today.")
        return
    
    print(f"[INFO] Found {len(data['aircraft'])} unique aircraft")
    print("[INFO] Building summary prompt...")
    prompt = build_summary_prompt(data)
    
    print("[INFO] Generating summary with OpenAI...")
    summary = generate_summary(prompt)
    
    write_summary(summary)
    print(f"[✅] Summary written to {SUMMARY_PATH}")
    
    # Print a preview
    print("\n--- Summary Preview ---")
    print(summary[:500] + "..." if len(summary) > 500 else summary)


if __name__ == "__main__":
    main()
