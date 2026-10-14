@echo off
REM ---------------------------------------------------------------
REM  Installs GPU (CUDA) support for Camera Detection.
REM  Run once from the same Python environment you use for main.py.
REM ---------------------------------------------------------------
cd /d "%~dp0"
echo.
echo  Checking for an NVIDIA GPU...
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo  [x] nvidia-smi not found. Install the NVIDIA driver first: https://www.nvidia.com/drivers
  goto :end
)
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
echo.
python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
if not errorlevel 1 (
  echo  [OK] PyTorch can already use the GPU:
  python -c "import torch; print('      ', torch.__version__, '-', torch.cuda.get_device_name(0))"
  goto :end
)

for %%C in (cu130 cu128 cu126) do (
  echo  Installing PyTorch with CUDA build %%C ...
  python -m pip install --upgrade --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/%%C
  python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
  if not errorlevel 1 goto :ok
  echo  [!] %%C did not work on this machine, trying the next one...
)
echo.
echo  [x] Could not enable the GPU automatically.
echo      Get the exact command for Windows / pip / CUDA at https://pytorch.org/get-started/locally/
goto :end

:ok
echo.
echo  [OK] GPU support installed:
python -c "import torch; print('      ', torch.__version__, '-', torch.cuda.get_device_name(0))"
echo      Restart the ground station. Camera Detection will show "Compute: GPU".

:end
echo.
pause
