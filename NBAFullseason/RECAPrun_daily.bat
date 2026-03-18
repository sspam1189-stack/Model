@echo off
setlocal

REM --- env vars your bot needs ---
set GMAIL_USER=YOUR_GMAIL_ADDRESS
set GMAIL_APP_PASSWORD=YOUR_GMAIL_APP_PASSWORD
set TO_EMAIL=YOUR_RECIPIENT_EMAIL
set ODDS_API_KEY=YOUR_ODDS_API_KEY

cd /d "C:\Users\HenryVM\Desktop\nba_picks_daily_bot"
if not exist "logs" mkdir "logs"

"C:\Program Files\nodejs\node.exe" "scripts\RECAPrun_daily.mjs" >> "logs\daily.log" 2>&1

endlocal