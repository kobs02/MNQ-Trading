@echo off
rem MNQ Trader 서버를 켠다. 이 창을 닫으면 서버가 꺼진다(가상 계좌의 자동매매도 멈춘다).
chcp 65001 >nul
title MNQ Trader 서버
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem 이미 켜져 있으면 브라우저만 연다
netstat -ano | findstr "127.0.0.1:8775" | findstr LISTENING >nul && (
  echo MNQ Trader 서버가 이미 켜져 있습니다. 브라우저만 엽니다.
  start "" "http://127.0.0.1:8775"
  timeout /t 3 >nul
  exit /b
)
echo MNQ Trader 서버를 켭니다: http://127.0.0.1:8775
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8775"
".venv\Scripts\python.exe" -m mnq.server
echo.
echo 서버가 꺼졌습니다. 아무 키나 누르면 창을 닫습니다.
pause >nul
