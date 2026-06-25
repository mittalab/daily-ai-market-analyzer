# Implementation Plan - Timezone-Aware Validation Date and Bhavcopy Local Caching

We will implement two improvements:
1. **Timezone-Aware IST End Date Check**:
   - Ensure the check for whether today is a trading day and time is >= 3:40 PM IST is executed in the `Asia/Kolkata` timezone.
   - Refactor duplicate logic in `new_validation/run_validation.py` to use a helper function `get_validation_end_date(holidays)`.
2. **Bhavcopy Caching in `tmp` Folder**:
   - Update `fetch_equity_bhavcopy` and `fetch_indices_bhavcopy` in `new_data_ingestion/nse_bhavcopy.py` to use a `tmp` folder in the project root.
   - Check if the bhavcopy CSV file exists locally first.
   - If it exists, read it from disk, log a line confirming cache hit, and proceed.
   - If not, download the file, save it to disk, log a line confirming download, and proceed.

## Proposed Changes

### 1. `new_validation/run_validation.py`
- Import `pytz`.
- Implement `get_validation_end_date(holidays)` using `pytz.timezone("Asia/Kolkata")`.
- Update `validate_and_heal` and `main` to use `get_validation_end_date`.

### 2. `new_data_ingestion/nse_bhavcopy.py`
- Define `_TMP_DIR = Path(__file__).parent.parent / "tmp"`.
- Ensure directory exists: `_TMP_DIR.mkdir(parents=True, exist_ok=True)`.
- Update `fetch_equity_bhavcopy` to check `_TMP_DIR / f"sec_bhavdata_full_{target.strftime('%d%m%Y')}.csv"`.
- Update `fetch_indices_bhavcopy` to check `_TMP_DIR / f"ind_close_all_{target.strftime('%d%m%Y')}.csv"`.
- Add log messages indicating whether files were found in `tmp` or downloaded.

## Verification
- Run tests or validation command to ensure the system compiles and works cleanly.
