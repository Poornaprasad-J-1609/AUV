@echo off
cd /d "%~dp0"
python train_camera_model.py %*
echo.
pause
