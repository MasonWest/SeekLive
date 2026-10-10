@echo off
chcp 936 >nul 2>&1
title Ê±¹âÖá SeekLive - Í£Ö¹

set "DIR=%~dp0"
cd /d "%DIR%"

set "PY="
set "PYOPTS="
where py >nul 2>&1 && set "PY=py"
if defined PY set "PYOPTS=-3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [´íÎó] Î´¼ì²âµ½ Python
    pause
    exit /b 1
)

%PY% %PYOPTS% "server.py" --stop
echo.
pause
