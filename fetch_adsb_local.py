#!/usr/bin/env python3

import requests
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Configuration ---
DUMP1090_URL = "http://localhost:8080/data/aircraft.json"
DB_PATH = Path.home() / "adsb-analytics" / "database" / "adsb_data.db"

# Ensure DB folder exists
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- SQL Setup ---
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS aircraft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    hex TEXT,
    flight TEXT,
    lat REAL,
    lon REAL,
    alt_baro INTEGER,
    track REAL,
    speed INTEGER,
    squawk TEXT,
    category TEXT,
    rssi REAL
)
"""

INSERT_SQL = """
INSERT INTO aircraft (timestamp, hex, flight, lat, lon, alt_baro, track, speed, squawk, category, rssi)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# --- Fetch ADS-B JSON ---
def fetch_adsb_data() -> list[dict[str, Any]]:
    try:
        response = requests.get(DUMP1090_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("aircraft", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[ERROR] Failed to fetch or parse data: {e}")
        return []

# --- Store in SQLite ---
def store_data(aircraft_list: list[dict[str, Any]]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE_SQL)

        now = datetime.now(timezone.utc).isoformat()
        stored_count = 0
        position_count = 0

        for aircraft in aircraft_list:
            # Store ALL aircraft with hex codes (even without position)
            if aircraft.get("hex"):
                lat = aircraft.get("lat")
                lon = aircraft.get("lon")
                
                cursor.execute(INSERT_SQL, (
                    now,
                    aircraft.get("hex").upper() if aircraft.get("hex") else None,
                    aircraft.get("flight", "").strip() if aircraft.get("flight") else None,
                    lat,
                    lon,
                    aircraft.get("alt_baro"),
                    aircraft.get("track"),
                    aircraft.get("gs"),  # Fixed: was 'speed', should be 'gs'
                    aircraft.get("squawk"),
                    aircraft.get("category"),
                    aircraft.get("rssi")
                ))
                stored_count += 1
                if lat and lon:
                    position_count += 1

        conn.commit()
        print(f"[INFO] Stored {stored_count} aircraft ({position_count} with position) at {now}")

def main() -> None:
    aircraft_data = fetch_adsb_data()
    if aircraft_data:
        store_data(aircraft_data)
    else:
        print("[INFO] No aircraft data to store.")

if __name__ == "__main__":
    main()
