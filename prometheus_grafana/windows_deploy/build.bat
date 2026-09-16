@echo off
REM ========================================================
REM UCM Observability Deploy Tool - Windows build script
REM Usage: double-click build.bat or run build.bat
REM ========================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PROJ_ROOT=%~dp0..\.."

echo =============================================
echo  UCM Observability - building EXE ...
echo =============================================

REM 1. Install PyInstaller
python -m pip install pyinstaller --quiet

REM 2. Verify Python and PyInstaller
python --version
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller not found. Run: pip install pyinstaller
    pause
    exit /b 1
)

REM 3. Detect conda prefix (tcl/tk DLLs live under Library\bin)
set "CONDA_PREFIX=%CONDA_PREFIX%"
if "%CONDA_PREFIX%"=="" (
    for /f "tokens=*" %%i in ('python -c "import sys; print(sys.prefix)"') do set "CONDA_PREFIX=%%i"
)
set "DLL_DIR=%CONDA_PREFIX%\Library\bin"

set "DLL_FLAGS="
if exist "%DLL_DIR%\tcl86t.dll" (
    set "DLL_FLAGS=!DLL_FLAGS! --add-binary "%DLL_DIR%\tcl86t.dll;." --add-binary "%DLL_DIR%\tk86t.dll;.""
)
if exist "%DLL_DIR%\libcrypto-3-x64.dll" (
    set "DLL_FLAGS=!DLL_FLAGS! --add-binary "%DLL_DIR%\libcrypto-3-x64.dll;." --add-binary "%DLL_DIR%\libssl-3-x64.dll;.""
)
if exist "%DLL_DIR%\liblzma.dll" (
    set "DLL_FLAGS=!DLL_FLAGS! --add-binary "%DLL_DIR%\liblzma.dll;.""
)
if exist "%DLL_DIR%\libbz2.dll" (
    set "DLL_FLAGS=!DLL_FLAGS! --add-binary "%DLL_DIR%\libbz2.dll;.""
)

echo DLL flags: !DLL_FLAGS!

REM 4. Build from project root
cd /d "%PROJ_ROOT%"

python -m PyInstaller --noconfirm --onefile --windowed --name UCMObsDeploy --add-data "prometheus_grafana/prometheus.yml;." --add-data "prometheus_grafana/docker-compose.yaml;." --add-data "prometheus_grafana/grafana-datasource.yml;." --add-data "prometheus_grafana/grafana-dashboard-provider.yml;." --add-data "prometheus_grafana/dashboards;dashboards" !DLL_FLAGS! "prometheus_grafana/windows_deploy/deploy_client.pyw"

if errorlevel 1 (
    echo [ERROR] Build failed.
    rmdir /s /q "%PROJ_ROOT%\build" 2>nul
    del /q "%PROJ_ROOT%\UCMObsDeploy.spec" 2>nul
    pause
    exit /b 1
)

echo =============================================
echo  Build OK: %PROJ_ROOT%\dist\UCMObsDeploy.exe
echo =============================================

REM 5. Cleanup intermediate files
rmdir /s /q "%PROJ_ROOT%\build" 2>nul
del /q "%PROJ_ROOT%\UCMObsDeploy.spec" 2>nul

pause