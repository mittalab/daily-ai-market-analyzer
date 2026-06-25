# Data Ingestion Package (new_data_ingestion)

This package consolidates all data collection, scraping, and backfilling methods for the Swing Trading application.

## Directory Structure

- `orchestrator.py`: Entry point for scheduled and pipeline data gathering runs (`run_bhavcopy_job`, `run_snapshot_job`, `run_kite_data_fetch`).
- `ingestion_utils.py`: High-level operations for today's ingestion and multi-day backfills.
- `nse_option_chain.py`: Browser-imitating option chain client (Unified v3 API scraper).
- `fo_bhavcopy.py`: Historical derivative/F&O Bhavcopy downloader and parser (Jul-08-2024 onwards UDiFF & legacy).
- `nse_bhavcopy.py`: Equity constituent closing prices and sector indices downloader.
- `nse_fii_dii.py`: Daily FII/DII net flows scraper.
- `backfill_vix.py`: Historical India VIX series downloader.
- `kite_oauth.py`: Kite Connect OAuth token store and validation manager.
- `kite_ohlcv.py`: Daily OHLCV price fetching.
- `kite_oi.py`: Continuous futures OI and rollover analysis.

## Key Usage

### Today's Daily Ingestion
```python
from new_data_ingestion.ingestion_utils import ingest_today
ingest_today(symbol=None) # symbol=None runs for all target stocks and indices
```

### Backfilling Historical Ranges
```python
from new_data_ingestion.ingestion_utils import backfill_range
from datetime import date
backfill_range(symbol="ABB", start_date=date(2026, 6, 1), end_date=date(2026, 6, 25))
```
