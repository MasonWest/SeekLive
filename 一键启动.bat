@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
title 时光轴 SeekLive - 启动

echo ============================================================
echo            时光轴 SeekLive   ·   一键启动
echo ============================================================
echo.

set "DIR=%~dp0"
cd /d "%DIR%"
set "PORT=9000"

:: ---------- 查找 Python ----------
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [错误] 未检测到 Python
    echo        请安装 Python 3.8+ 并勾选 "Add Python to PATH"
    echo        https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: ---------- 依赖检查 ----------
%PY% -c "import PIL" >nul 2>&1
if errorlevel 1 (
    echo [1/3] 正在安装 Pillow ...
    %PY% -m pip install Pillow --quiet --disable-pip-version-check
)
%PY% -c "import pillow_heif" >nul 2>&1
if errorlevel 1 (
    echo [1/3] 正在安装 pillow-heif（HEIC 支持）...
    %PY% -m pip install pillow-heif --quiet --disable-pip-version-check
)
echo [1/3] 依赖就绪

:: ---------- 配置文件 ----------
if not exist "libraries.json" (
    %PY% "server.py" --init >nul 2>&1
    echo [2/3] 首次运行，已生成配置文件：
    echo       %DIR%libraries.json
    echo       请把里面的 root 改成你的照片目录，然后再次双击本脚本。
    echo.
    pause
    exit /b 0
)
echo [2/3] 配置文件就绪

:: ---------- 管理授权（可选） ----------
:: 浏览永远不需要口令；只有「删除 / 恢复 / 永久删除 / 清空回收站」需要管理授权。
:: 开启方式：在本目录放一个 admin.json（不要提交到版本库），内容形如：
::     { "adminToken": "你的口令" }
:: 也可用命令行参数临时覆盖： server.py --admin-token 你的口令
if exist "admin.json" (echo [管理] 已启用管理授权 - 来源 admin.json) else (echo [管理] 未配置管理口令 - 删除功能关闭，浏览不受影响)

:: ---------- 启动 ----------
echo [3/3] 正在启动服务...
echo.
echo   * 手机访问：看下面打印的「局域网」地址，手机连同一个 WiFi 打开
echo   * 停止服务：按 Ctrl+C，或双击「一键结束.bat」
echo.

:: 延迟 3 秒再打开浏览器，等服务把索引建好
start "" /min cmd /c "timeout /t 3 >nul & start http://127.0.0.1:%PORT%/"

%PY% "server.py" --port %PORT% --lan

echo.
echo 服务已退出。
pause
endlocal
