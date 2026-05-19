# Stock Screener

This project has two entry points:

- `python screener.py` fetches fundamentals, writes `screener_data.db`, exports Excel, and updates `winners_latest.txt`.
- `python -m streamlit run app.py` opens the interactive sector screener site.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Quick site launch on Windows:

```powershell
.\run_site.bat
```

The site reads the latest saved rows from `screener_data.db` first, then falls back to `_fetch_cache_v5_edgar.parquet`.
