@echo off
cd /d "E:\IA\SUPO CLIP"

docker compose up -d

timeout /t 5 /nobreak >nul

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app=http://localhost:3001

exit