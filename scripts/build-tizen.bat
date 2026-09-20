@echo off
rem Assemble the Tizen .wgt package from the shared server\tv web UI.
rem The .wgt is an unsigned zip - sign+install it with Tizen Studio or
rem   tizen package -t wgt -s <profile> -- tizen\www
setlocal
cd /d "%~dp0\.."

if exist tizen\www rmdir /s /q tizen\www
mkdir tizen\www

copy /y server\tv\index.html tizen\www\ >nul
copy /y server\tv\app.js     tizen\www\ >nul
copy /y server\tv\style.css  tizen\www\ >nul
copy /y tizen\config.xml     tizen\www\ >nul
copy /y roku\images\icon_hd.png tizen\www\icon.png >nul

if not exist dist mkdir dist
powershell -command "Compress-Archive -Path 'tizen\www\*' -DestinationPath 'dist\pi-iptv-dvr-tizen.zip' -Force"
if exist dist\pi-iptv-dvr-tizen.wgt del dist\pi-iptv-dvr-tizen.wgt
ren dist\pi-iptv-dvr-tizen.zip pi-iptv-dvr-tizen.wgt

echo Built dist\pi-iptv-dvr-tizen.wgt
echo.
echo To install on the TV:
echo   1. Enable Developer Mode on the TV (Apps -^> type 12345 -^> On -^> PC IP -^> reboot)
echo   2. Sign the package (Tizen Studio certificate, then):
echo        tizen package -t wgt -s ^<profile^> -- tizen\www
echo   3. Install:
echo        tizen install -n pi-iptv-dvr-tizen.wgt -t ^<TV-name-or-ip^>
echo Or open http://^<pi-ip^>:8080/tv in the Samsung browser - same app, no install.
