@echo off
rem MNQ Trader 서버를 켠다. 이 창을 닫으면 서버가 꺼진다(가상 계좌의 자동매매도 멈춘다).
chcp 65001 >nul
title MNQ Trader 서버
cd /d "%~dp0"
rem 시작 안내와 로그는 서버가 컬러 바 모양으로 찍는다(mnq/console.py). 이미 켜져 있으면 브라우저만 열고 끝난다
".venv\Scripts\python.exe" main.py
echo.
echo 서버가 꺼졌습니다. 아무 키나 누르면 창을 닫습니다.
pause >nul
