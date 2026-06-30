@echo off
start "" ".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8001
timeout /t 5 /nobreak > nul
echo done
