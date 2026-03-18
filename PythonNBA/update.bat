@echo off
setlocal

set GMAIL_USER=YOUR_GMAIL_ADDRESS
set GMAIL_APP_PASSWORD=YOUR_GMAIL_APP_PASSWORD
set TO_EMAIL=YOUR_RECIPIENT_EMAIL
set ODDS_API_KEY=YOUR_ODDS_API_KEY

REM --- Production ---
cd /d "C:\Users\HenryVM\Desktop\nba_picks_daily_bot"
if not exist "logs" mkdir "logs"
"C:\Program Files\nodejs\node.exe" "scripts\update.mjs" >> "logs\daily.log" 2>&1

REM --- Full Season Backfill ---
cd /d "C:\Users\HenryVM\Desktop\nba_picks_daily_botfullseason"
if not exist "logs" mkdir "logs"
"C:\Program Files\nodejs\node.exe" "scripts\update.mjs" >> "logs\daily.log" 2>&1

REM --- NCAA ---
cd /d "C:\Users\HenryVM\Desktop\ncaa_picks_daily_bot"
if not exist "logs" mkdir "logs"
"C:\Program Files\nodejs\node.exe" "scripts\update.mjs" >> "logs\daily.log" 2>&1

endlocal