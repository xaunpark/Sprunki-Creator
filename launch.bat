@echo off
REM launch.bat - Sprunki Ultimate Master Windows Launcher
REM Double-click this file to launch the application

title Sprunki Ultimate Master Launcher

echo.
echo ========================================
echo    Sprunki Ultimate Master Launcher
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo.
    echo Please install Python 3.8+ from: https://python.org
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)

echo Python found: 
python --version

REM Check if we're in the correct directory
if not exist "sprunki_ultimate_master.py" (
    echo.
    echo ERROR: sprunki_ultimate_master.py not found
    echo Please make sure you're running this from the project directory
    echo.
    pause
    exit /b 1
)

echo Project files found: OK
echo.

REM Try to launch the application
echo Starting Sprunki Ultimate Master...
echo.

REM First try the launcher script
if exist "launch.py" (
    echo Using launcher script...
    python launch.py
) else (
    REM Fallback to direct launch
    echo Direct launch...
    python sprunki_ultimate_master.py
)

REM Check if there was an error
if errorlevel 1 (
    echo.
    echo ========================================
    echo            ERROR OCCURRED
    echo ========================================
    echo.
    echo The application encountered an error.
    echo.
    echo Possible solutions:
    echo 1. Install missing packages: pip install -r requirements.txt
    echo 2. Run setup: python setup.py  
    echo 3. Run tests: python test_setup.py
    echo 4. Check README.md for detailed instructions
    echo.
    echo Error details may be in the console output above.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo        Application Closed
echo ========================================
echo.
pause