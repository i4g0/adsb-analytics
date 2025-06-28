# ADS-B Analytics

A comprehensive ADS-B (Automatic Dependent Surveillance-Broadcast) aircraft tracking and analysis system for Raspberry Pi. This project collects real-time aircraft data from your local ADS-B receiver, enriches it with registration information, and generates daily summaries using AI.

## Features

- Real-time aircraft data collection from dump1090
- Aircraft enrichment with registration, type, and operator information
- Daily AI-powered summaries of air traffic patterns
- SQLite database for historical data storage
- LCD display integration for live statistics
- Automated reporting via cron jobs

## System Architecture

```
dump1090 → fetch_adsb_local.py → SQLite Database
                                      ↓
                              enrich_aircraft.py
                                      ↓
                              summarize_daily.py → OpenAI
                                      ↓
                              show_summary_popup.py → Display
```

## Prerequisites

- Raspberry Pi with ADS-B receiver (RTL-SDR)
- dump1090 or dump1090-fa running on port 8080
- Python 3.8 or higher
- OpenAI API key

## Installation

1. **Clone the repository:**
```bash
git clone https://github.com/i4g0/adsb-analytics.git
cd adsb-analytics
```

2. **Create and activate a virtual environment:**
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Set up environment variables:**
```bash
# Create .env file in the project root
echo "OPENAI_API_KEY=your_api_key_here" > .env
```

5. **Create directory structure:**
```bash
mkdir -p ~/adsb-analytics/database
mkdir -p ~/adsb-analytics/summaries
mkdir -p ~/adsb-analytics/logs
```

6. **Initialize the database:**
```bash
python3 fetch_adsb_local.py  # Run once to create tables
```

## Configuration

### Cron Setup

Add these entries to your crontab (`crontab -e`):

```bash
# fetch ADS-B data every 30 seconds
* * * * * /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/fetch_adsb_local.py > /dev/null 2>&1
* * * * * sleep 30 && /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/fetch_adsb_local.py > /dev/null 2>&1

# Enrich TODAY's aircraft every hour (quick, focuses on active aircraft)
15 * * * * /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/enrich_aircraft.py --today-only >> /home/pi/adsb-analytics/logs/enrich.log 2>&1

# Enrich last 7 days of aircraft twice daily (catches aircraft that appear regularly)
30 6,18 * * * /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/enrich_aircraft.py --days 7 --batch-size 1000 >> /home/pi/adsb-analytics/logs/enrich.log 2>&1

# build summary at 4:55 PM
55 16 * * * /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/summarize_daily.py >> /home/pi/adsb-analytics/logs/summarize.log 2>&1

# send summary at 5:00 PM
0 17 * * * DISPLAY=:0 XAUTHORITY=/home/pi/.Xauthority /home/pi/adsb-analytics/venv/bin/python /home/pi/adsb-analytics/show_summary_popup.py >> /home/pi/adsb-analytics/logs/popup.log 2>&1

```

### dump1090 Configuration

Ensure dump1090 is configured to serve JSON data on port 8080. The default URL is:
```
http://localhost:8080/data/aircraft.json
```

If your dump1090 uses a different port or path, update `DUMP1090_URL` in `fetch_adsb_local.py`.

## Scripts Overview

### fetch_adsb_local.py
Fetches real-time aircraft data from dump1090 and stores it in SQLite.
- Runs every minute via cron
- Stores all aircraft with hex codes (even those without position data)
- Records: hex, flight, position, altitude, speed, squawk, RSSI

### enrich_aircraft.py
Enriches aircraft data with registration and operator information.
- Uses free ADS-B Database API (no key required)
- Rate-limited to respect API limits
- Command line options:
  - `--debug`: Show detailed debug output
  - `--batch-size`: Set batch size for updating 
  - `--backfill`: Process all unenriched aircraft (one-time operation)
  - `--help`: Show usage information

### summarize_daily.py
Generates AI-powered daily summaries using OpenAI GPT-4.
- Identifies interesting aircraft (military, police, medical)
- Analyzes traffic patterns by operator and aircraft type
- Creates human-readable reports
- Saves summaries with timestamps

### show_summary_popup.py
Displays the daily summary in a Tkinter popup window.
- Reads from `~/adsb-analytics/summaries/today.txt`
- Suitable for LCD/monitor display integration

## Database Schema

### aircraft table
Stores raw ADS-B messages from dump1090.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key, auto-increment |
| timestamp | TEXT | ISO format UTC timestamp |
| hex | TEXT | ICAO 24-bit address (uppercase) |
| flight | TEXT | Flight number/callsign |
| lat | REAL | Latitude |
| lon | REAL | Longitude |
| alt_baro | INTEGER | Barometric altitude (feet) |
| track | REAL | Track angle (degrees) |
| speed | INTEGER | Ground speed (knots) |
| squawk | TEXT | Transponder code |
| category | TEXT | Aircraft category |
| rssi | REAL | Signal strength |

### aircraft_enriched table
Stores enrichment data from external APIs.

| Column | Type | Description |
|--------|------|-------------|
| hex | TEXT | Primary key, ICAO address |
| registration | TEXT | Tail number |
| type | TEXT | Aircraft type code |
| manufacturer | TEXT | Aircraft manufacturer |
| operator | TEXT | Operating airline/owner |
| origin_country | TEXT | Country of registration |
| last_updated | TEXT | Last enrichment timestamp |
| source | TEXT | Data source identifier |

## Usage Examples

### Manual Operations

```bash
# Activate virtual environment first
source venv/bin/activate

# Test data collection
python3 fetch_adsb_local.py

# Enrich today's aircraft
python3 enrich_aircraft.py --today-only

# Enrich last 7 days with debug output
python3 enrich_aircraft.py --days 7 --debug

# Generate today's summary manually
python3 summarize_daily.py

# Full backfill (one-time operation for initial setup)
python3 enrich_aircraft.py --backfill
```

### Database Queries

```bash
# View database statistics
sqlite3 ~/adsb-analytics/database/adsb_data.db "SELECT COUNT(DISTINCT hex) FROM aircraft;"

# Recent aircraft
sqlite3 ~/adsb-analytics/database/adsb_data.db "SELECT hex, flight, MAX(timestamp) FROM aircraft GROUP BY hex ORDER BY MAX(timestamp) DESC LIMIT 10;"

# Check enrichment status
sqlite3 ~/adsb-analytics/database/adsb_data.db "SELECT COUNT(*) as total, COUNT(registration) as enriched FROM aircraft_enriched;"
```

## Troubleshooting

### No aircraft data appearing
1. Verify dump1090 is running: `systemctl status dump1090-fa`
2. Check dump1090 JSON endpoint: `curl http://localhost:8080/data/aircraft.json`
3. Verify database permissions: `ls -la ~/adsb-analytics/database/`

### Enrichment not working
1. Check internet connectivity
2. Verify hex codes are uppercase in database
3. Check API responses: `python3 enrich_aircraft.py --debug`
4. Review logs: `tail -f ~/adsb-analytics/logs/enrich.log`

### Time/timezone issues
1. Verify system time: `timedatectl`
2. Ensure NTP is synchronized
3. Check timestamp format in database (should be UTC)

### Summary generation fails
1. Verify OpenAI API key in .env file
2. Check API quota/billing
3. Review logs: `tail -f ~/adsb-analytics/logs/summary.log`

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is licensed under the GPL License - see the LICENSE file for details.

## Acknowledgments

- dump1090 community for ADS-B decoding
- ADS-B Database for free aircraft data API
- OpenAI for GPT-4 API
