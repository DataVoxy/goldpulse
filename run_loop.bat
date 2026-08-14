@echo off
title GoldPulse Strategy
cd /d C:\Users\tommi\Downloads\GoldPulse

:loop
py core/strategy.py
timeout /t 900 /nobreak >nul
goto loop
