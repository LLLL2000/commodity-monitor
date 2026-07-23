@echo off
REM Always-on launcher for the live collector (auto-restart loop + logging).
REM Started hidden by run_collector_hidden.vbs via the CommodityMonitorCollector
REM scheduled task. Reads AISSTREAM_KEY from ..\.env automatically.
setlocal
set "REPO=C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor"
set "PY=C:\Users\consu\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe"
set "LOG=%REPO%\data\collector.live.log"
cd /d "%REPO%"

:loop
echo [%date% %time%] starting collector --live >> "%LOG%"
"%PY%" -u -m collector.collector --live >> "%LOG%" 2>&1
echo [%date% %time%] collector exited (code %errorlevel%); restarting in 15s >> "%LOG%"
timeout /t 15 /nobreak > nul
goto loop
