#!/usr/bin/env python3

import sqlite3
import requests
import time
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

DB_PATH = Path.home() / "adsb-analytics" / "database" / "adsb_data.db"

# Command line arguments
DEBUG = "--debug" in sys.argv
BACKFILL = "--backfill" in sys.argv

# Create enrichment table
CREATE_ENRICHMENT_TABLE = """
CREATE TABLE IF NOT EXISTS aircraft_enriched (
    hex TEXT PRIMARY KEY,
    registration TEXT,
    type TEXT,
    manufacturer TEXT,
    operator TEXT,
    origin_country TEXT,
    last_updated TEXT,
    source TEXT
)
"""

def debug_print(msg):
    """Print debug messages if debug mode is on."""
    if DEBUG:
        print(f"[DEBUG] {msg}")

def setup_enrichment_table():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(CREATE_ENRICHMENT_TABLE)
        conn.commit()

def get_unenriched_hex_codes(limit: Optional[int] = None) -> list[str]:
    """Get hex codes that haven't been enriched yet."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Build query
        query = """
            SELECT DISTINCT a.hex 
            FROM aircraft a
            LEFT JOIN aircraft_enriched e ON a.hex = e.hex
            WHERE (e.hex IS NULL OR e.registration IS NULL) 
              AND a.hex IS NOT NULL
            ORDER BY a.hex
        """
        
        if limit:
            query += f" LIMIT {limit}"
            
        cursor.execute(query)
        codes = [row[0] for row in cursor.fetchall()]
        
        if DEBUG and codes:
            debug_print(f"Found {len(codes)} hex codes. First 5: {codes[:5]}")
        return codes

def get_stats() -> tuple:
    """Get enrichment statistics."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT hex) FROM aircraft")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM aircraft_enriched WHERE registration IS NOT NULL")
        enriched = cursor.fetchone()[0]
        return total, enriched

def enrich_from_adsbdb(hex_code: str) -> Optional[Dict]:
    """Use the free ADS-B Database API - no key required!"""
    try:
        # Make sure hex code is uppercase and clean
        hex_clean = hex_code.strip().upper()
        url = f"https://api.adsbdb.com/v0/aircraft/{hex_clean}"
        
        debug_print(f"Requesting URL: {url}")
        
        headers = {'User-Agent': 'adsb-analytics/1.0'}
        response = requests.get(url, timeout=10, headers=headers)
        
        debug_print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if DEBUG:
                debug_print(f"Response JSON:\n{json.dumps(data, indent=2)}")
            
            # The structure is response.aircraft
            if 'response' in data and isinstance(data['response'], dict):
                ac = data['response'].get('aircraft')
                if ac:
                    return {
                        'registration': ac.get('registration'),
                        'type': ac.get('icao_type'),  # Use icao_type for consistency
                        'manufacturer': ac.get('manufacturer'),
                        'operator': ac.get('registered_owner'),  # This is the actual field name
                        'origin_country': ac.get('registered_owner_country_name'),
                        'source': 'adsbdb'
                    }
            elif data.get('response') == 'unknown aircraft':
                debug_print(f"Aircraft {hex_code} not in database")
                
        elif response.status_code == 404:
            debug_print(f"Aircraft {hex_code} not found (404)")
            
    except Exception as e:
        print(f"[ERROR] Failed for {hex_code}: {type(e).__name__}: {e}")
    
    return None

def save_enrichment(hex_code: str, data: Dict):
    """Save enrichment data to database."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO aircraft_enriched 
            (hex, registration, type, manufacturer, operator, origin_country, last_updated, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hex_code.upper(),
            data.get('registration'),
            data.get('type'),
            data.get('manufacturer'),
            data.get('operator'),
            data.get('origin_country'),
            datetime.now(timezone.utc).isoformat(),
            data.get('source', 'unknown')
        ))
        conn.commit()

def main():
    if DEBUG:
        print("[DEBUG] Debug mode enabled")
        print(f"[DEBUG] Database path: {DB_PATH}")
    
    if BACKFILL:
        print("[INFO] BACKFILL mode - processing ALL unenriched aircraft")
        print("[INFO] This will take a while but will respect API rate limits")
    
    setup_enrichment_table()
    
    # Show current stats
    total, enriched = get_stats()
    print(f"[INFO] Database stats: {enriched}/{total} aircraft enriched")
    
    # Get batch of unenriched aircraft
    if BACKFILL:
        limit = None  # Get all unenriched
    elif DEBUG:
        limit = 5
    else:
        limit = 50  # Normal batch size
        
    hex_codes = get_unenriched_hex_codes(limit=limit)
    
    if not hex_codes:
        print("[INFO] All aircraft are already enriched!")
        return
        
    print(f"[INFO] Found {len(hex_codes)} aircraft to enrich")
    
    if BACKFILL and len(hex_codes) > 100:
        # Estimate time for backfill
        seconds_needed = len(hex_codes) * 0.5  # 0.5 seconds per aircraft (0.3 + buffer)
        minutes = int(seconds_needed / 60)
        print(f"[INFO] Estimated time: ~{minutes} minutes")
        print("[INFO] You can stop anytime with Ctrl+C (progress is saved)")
        time.sleep(3)  # Give user time to read
    
    success_count = 0
    not_found_count = 0
    start_time = time.time()
    
    try:
        for i, hex_code in enumerate(hex_codes):
            # Progress display logic
            show_progress = False
            if DEBUG:
                show_progress = True
            elif BACKFILL and (i % 50 == 0 or i == len(hex_codes) - 1):
                show_progress = True
                # Show time estimate
                if i > 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed
                    remaining = (len(hex_codes) - i) / rate
                    print(f"\n[PROGRESS] {i}/{len(hex_codes)} ({i*100//len(hex_codes)}%) - "
                          f"~{int(remaining/60)} minutes remaining", flush=True)
            elif not BACKFILL and i % 10 == 0:
                show_progress = True
            
            if show_progress:
                print(f"[{i+1}/{len(hex_codes)}] Enriching {hex_code}...", end='', flush=True)
            
            data = enrich_from_adsbdb(hex_code)
            
            if data and data.get('registration'):  # We got useful data
                save_enrichment(hex_code, data)
                if show_progress:
                    print(f" ✓ {data['registration']} ({data.get('type', 'Unknown')}) - {data.get('operator', 'Unknown operator')}")
                success_count += 1
            else:
                # Save empty record so we don't keep retrying
                save_enrichment(hex_code, {'source': 'not_found'})
                if show_progress and DEBUG:
                    print(" ✗ Not found")
                not_found_count += 1
            
            # Rate limiting - be nice to free API
            if i < len(hex_codes) - 1:  # Don't sleep after last one
                if BACKFILL:
                    time.sleep(0.5)  # Slower for backfill to be extra respectful
                else:
                    time.sleep(0.3)  # Normal rate
                    
    except KeyboardInterrupt:
        print(f"\n\n[STOPPED] User interrupted. Progress saved.")
        print(f"[INFO] Enriched {success_count} aircraft before stopping")
    
    # Final stats
    elapsed_time = int(time.time() - start_time)
    print(f"\n[DONE] Successfully enriched {success_count}/{len(hex_codes)} aircraft ({not_found_count} not found)")
    print(f"[TIME] Total time: {elapsed_time//60}m {elapsed_time%60}s")
    
    # Show new stats
    total, enriched = get_stats()
    print(f"[INFO] New stats: {enriched}/{total} aircraft enriched")
    
    # Show some examples of enriched aircraft
    if success_count > 0 and not DEBUG:
        print("\n[INFO] Sample enriched aircraft:")
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hex, registration, type, operator 
                FROM aircraft_enriched 
                WHERE registration IS NOT NULL 
                ORDER BY last_updated DESC 
                LIMIT 5
            """)
            for row in cursor.fetchall():
                print(f"  {row[0]}: {row[1]} ({row[2]}) - {row[3]}")

if __name__ == "__main__":
    # Show usage if needed
    if "--help" in sys.argv:
        print("Usage: python3 enrich_aircraft.py [options]")
        print("Options:")
        print("  --debug     Show detailed debug output (processes 5 aircraft)")
        print("  --backfill  Process ALL unenriched aircraft (respectfully)")
        print("  --help      Show this help message")
        print("\nNormal usage processes 50 aircraft at a time.")
        sys.exit(0)
        
    main()
