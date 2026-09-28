# Horse Racing Analytics

## Windows quick start

This ZIP contains the built local application. Install Python 3.12; Node.js is not needed to run it.

1. Extract the ZIP and open PowerShell in the extracted `horse-racing-analytics-<version>` folder.
2. Create the backend environment and install the app:

   `cd backend`

   `python -m venv .venv`

   `.\.venv\Scripts\python.exe -m pip install .`

3. Return to the extracted folder and start the app:

   `cd ..`

   `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1`

4. Open `http://127.0.0.1:8000` in your browser. The app listens on localhost and is not exposed as a public website.

SQLite data and backups are stored under `%LOCALAPPDATA%\HorseRacingAnalytics`, outside the extracted ZIP.

## Example CSV files

`examples/sample-race.csv` and `examples/sample-results.csv` contain fabricated demonstration data. They are not real JRA races, odds, or results. Use them only to inspect the CSV format, not for analysis or model evaluation.
