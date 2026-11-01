@echo off
wsl.exe -d StationLab -u stationuser -- ssh -T -o BatchMode=yes -o ConnectTimeout=8 -F /home/stationuser/kas-live/ssh_config station sh -s < "%~dp0disable_router.sh"
if errorlevel 1 echo Recovery was not confirmed. Check the station connection.
pause
