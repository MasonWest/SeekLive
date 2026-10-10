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
:: ★ 为什么用「py -3」而不是「py」：
::   server.py 首行是 #!/usr/bin/env python3，Windows 的 py 启动器
::   会按这行 shebang 去找 python3；一旦命中 Microsoft Store 的
::   占位程序（...\WindowsApps\python3.exe），它会【静默退出】并返回
::   错误码 9009，控制台一个字都不打印（表现就是「双击后
::   什么都没发生」）。显式带上 -3 即可跳过 shebang 解析。
set "PY="
set "PYOPTS="
where py >nul 2>&1 && set "PY=py"
if defined PY set "PYOPTS=-3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [错误] 未检测到 Python
    echo        请安装 Python 3.8+ 并勾选 "Add Python to PATH"
    echo        https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: 校验解释器真的能跑（挡住 Store 占位程序那种「静默 9009」）
%PY% %PYOPTS% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [错误] 找到了 Python 启动器，但它无法运行脚本。
    echo       最常见原因：python3 指向了 Microsoft Store 的占位程序。
    echo       解决：Windows 设置 - 应用 - 高级应用设置 - 应用执行别名，
    echo             把 python.exe / python3.exe 两个别名关闭，然后重试本脚本。
    echo.
    pause
    exit /b 1
)

:: ---------- 依赖检查 ----------
%PY% %PYOPTS% -c "import PIL" >nul 2>&1
if errorlevel 1 (
    echo [1/3] 正在安装 Pillow ...
    %PY% %PYOPTS% -m pip install Pillow --quiet --disable-pip-version-check
)
%PY% %PYOPTS% -c "import pillow_heif" >nul 2>&1
if errorlevel 1 (
    echo [1/3] 正在安装 pillow-heif（HEIC 支持）...
    %PY% %PYOPTS% -m pip install pillow-heif --quiet --disable-pip-version-check
)
echo [1/3] 依赖就绪

:: ---------- 配置文件 ----------
if not exist "libraries.json" (
    %PY% %PYOPTS% "server.py" --init >nul 2>&1
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

%PY% %PYOPTS% "server.py" --port %PORT% --lan
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo [错误] 服务未能启动，退出码 = %RC%
    echo       9009 = 没找到可用的 Python（多为 Microsoft Store 占位程序）：
    echo              设置 - 应用 - 高级应用设置 - 应用执行别名，
    echo              关闭 python.exe / python3.exe 后重试。
    echo       其它退出码：请看上面的输出，或 logs\server.log。
    echo.
)
echo 服务已退出（退出码 %RC%）
pause
endlocal
