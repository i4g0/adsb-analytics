#!/usr/bin/env python3

import sqlite3
import requests
import time
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict

DB_PATH = Path.home() / "adsb-analytics" / "database" / "adsb_data.db"

# Command line arguments
DEBUG = "--debug" in sys.argv
RECENT_DAYS = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
BATCH_SIZE = int(sys.argv[sys.argv.index("--batch-size") + 1]) if "--batch-size" in sys.argv else 100

# Create enrichment table (same as before)
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

def get_recent_unenriched_hex_codes(days: int = 7, limit: int = 100) -> list[str]:
    """Get hex codes seen in the last N days that haven't been enriched."""
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Get aircraft seen recently that don't have enrichment
        cursor.execute("""
            SELECT DISTINCT a.hex, MAX(a.timestamp) as last_seen, COUNT(*) as ping_count
            FROM aircraft a
            LEFT JOIN aircraft_enriched e ON a.hex = e.hex
            WHERE a.timestamp > ? 
              AND (e.hex IS NULL OR e.registration IS NULL)
              AND a.hex IS NOT NULL
            GROUP BY a.hex
            ORDER BY last_seen DESC, ping_count DESC
            LIMIT ?
        """, (cutoff_date, limit))
        
        results = cursor.fetchall()
        
        if DEBUG and results:
            debug_print(f"Found {len(results)} recent aircraft to enrich")
            debug_print(f"Most recent: {results[0][0]} last seen {results[0][1]}")
        
        return [row[0] for row in results]

def get_todays_unenriched_hex_codes(limit: int = 50) -> list[str]:
    """Get hex codes from today that need enrichment."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT a.hex, COUNT(*) as ping_count
            FROM aircraft a
            LEFT JOIN aircraft_enriched e ON a.hex = e.hex
            WHERE a.timestamp >= ?
              AND (e.hex IS NULL OR e.registration IS NULL)
              AND a.hex IS NOT NULL
            GROUP BY a.hex
            ORDER BY ping_count DESC
            LIMIT ?
        """, (today_start, limit))
        
        results = cursor.fetchall()
        return [row[0] for row in results]

def get_stats() -> dict:
    """Get enrichment statistics."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Overall stats
        cursor.execute("SELECT COUNT(DISTINCT hex) FROM aircraft")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM aircraft_enriched WHERE registration IS NOT NULL")
        enriched = cursor.fetchone()[0]
        
        # Recent stats
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cursor.execute("""
            SELECT COUNT(DISTINCT a.hex) as recent_total,
                   COUNT(DISTINCT CASE WHEN e.registration IS NOT NULL THEN a.hex END) as recent_enriched
            FROM aircraft a
            LEFT JOIN aircraft_enriched e ON a.hex = e.hex
            WHERE a.timestamp > ?
        """, (recent_cutoff,))
        
        recent_total, recent_enriched = cursor.fetchone()
        
        # Today's stats
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor.execute("""
            SELECT COUNT(DISTINCT a.hex) as today_total,
                   COUNT(DISTINCT CASE WHEN e.registration IS NOT NULL THEN a.hex END) as today_enriched
            FROM aircraft a
            LEFT JOIN aircraft_enriched e ON a.hex = e.hex
            WHERE a.timestamp >= ?
        """, (today_start,))
        
        today_total, today_enriched = cursor.fetchone()
        
        return {
            'total': total,
            'enriched': enriched,
            'recent_total': recent_total,
            'recent_enriched': recent_enriched,
            'today_total': today_total,
            'today_enriched': today_enriched
        }

def enrich_from_adsbdb(hex_code: str) -> Optional[Dict]:
    """Use the free ADS-B Database API - no key required!"""
    try:
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
            
            if 'response' in data and isinstance(data['response'], dict):
                ac = data['response'].get('aircraft')
                if ac:
                    return {
                        'registration': ac.get('registration'),
                        'type': ac.get('icao_type'),
                        'manufacturer': ac.get('manufacturer'),
                        'operator': ac.get('registered_owner'),
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
    if "--help" in sys.argv:
        print("Usage: python3 enrich_recent_aircraft.py [options]")
        print("Options:")
        print("  --debug         Show detailed debug output")
        print("  --days N        Look back N days for aircraft (default: 7)")
        print("  --today-only    Only enrich aircraft seen today")
        print("  --batch-size    Manually set batch size (default: 100)")
        print("  --help          Show this help message")
        print("\nThis script prioritizes recently seen aircraft for enrichment.")
        sys.exit(0)
    
    setup_enrichment_table()
    
    # Show current stats
    stats = get_stats()
    print(f"[INFO] Database statistics:")
    print(f"  Total: {stats['enriched']}/{stats['total']} aircraft enriched")
    print(f"  Last 7 days: {stats['recent_enriched']}/{stats['recent_total']} enriched")
    print(f"  Today: {stats['today_enriched']}/{stats['today_total']} enriched")

    # Get aircraft to enrich
    if "--today-only" in sys.argv:
        print(f"\n[INFO] Enriching today's aircraft only")
        hex_codes = get_todays_unenriched_hex_codes(limit=BATCH_SIZE)
    else:
        print(f"\n[INFO] Enriching aircraft from last {RECENT_DAYS} days")
        hex_codes = get_recent_unenriched_hex_codes(days=RECENT_DAYS, limit=BATCH_SIZE)
    
    if not hex_codes:
        print("[INFO] No recent aircraft need enrichment!")
        return
    
    print(f"[INFO] Found {len(hex_codes)} aircraft to enrich")
    
    success_count = 0
    not_found_count = 0
    start_time = time.time()
    
    for i, hex_code in enumerate(hex_codes):
        if i % 10 == 0 or DEBUG:
            print(f"[{i+1}/{len(hex_codes)}] Enriching {hex_code}...", end='', flush=True)
        
        data = enrich_from_adsbdb(hex_code)
        
        if data and data.get('registration'):
            save_enrichment(hex_code, data)
            if i % 10 == 0 or DEBUG:
                print(f" ✓ {data['registration']} ({data.get('type', 'Unknown')}) - {data.get('operator', 'Unknown operator')}")
            success_count += 1
        else:
            save_enrichment(hex_code, {'source': 'not_found'})
            if DEBUG:
                print(" ✗ Not found")
            not_found_count += 1
        
        # Rate limiting
        if i < len(hex_codes) - 1:
            time.sleep(0.3)
    
    # Final stats
    elapsed_time = int(time.time() - start_time)
    print(f"\n[DONE] Successfully enriched {success_count}/{len(hex_codes)} aircraft ({not_found_count} not found)")
    print(f"[TIME] Total time: {elapsed_time//60}m {elapsed_time%60}s")
    
    # Show new stats
    new_stats = get_stats()
    print(f"\n[INFO] Updated statistics:")
    print(f"  Today: {new_stats['today_enriched']}/{new_stats['today_total']} enriched")
    print(f"  Last 7 days: {new_stats['recent_enriched']}/{new_stats['recent_total']} enriched")

if __name__ == "__main__":
    main()
