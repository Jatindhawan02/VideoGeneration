@echo off
cd /d %~dp0
adb start-server
python main.py >> logs\run.log 2>&1
