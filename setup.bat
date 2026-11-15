@echo off
setlocal
cd /d "%~dp0"
title LightClip setup

echo ============================================
echo   LightClip setup - one-time installation
echo ============================================
echo.

python --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ not found. Installing via winget...
    winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
    echo.
    echo IMPORTANT: close this window and run setup.bat again so the PATH refreshes.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ffmpeg not found. Installing via winget - this can take a minute...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    echo If the next step cannot find ffmpeg, close this window and run setup.bat again.
)

echo Installing Python dependencies...
python -m pip install -r "%~dp0requirements.txt"

echo.
python "%~dp0lightclip.py" --setup %*

echo.
python "%~dp0lightclip.py" --doctor

echo.
echo All set. Clip something:
echo   python lightclip.py your-video.mp4
echo   python lightclip.py "https://youtu.be/..."
echo   python lightclip.py .\videos-folder
echo   python lightclip.py your-video.mp4 --heuristic    [zero-key, zero-cost mode]
pause
