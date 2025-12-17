@echo off
setlocal enabledelayedexpansion

:MENU
cls
echo 1. Choose Python version and create .venv
echo 2. Export requirements.txt
echo 3. Install from requirements.txt
echo 4. Exit
set /p choice="Enter your choice: "

if "%choice%"=="1" goto CHOOSE_PYTHON
if "%choice%"=="2" goto EXPORT_REQUIREMENTS
if "%choice%"=="3" goto INSTALL_REQUIREMENTS
if "%choice%"=="4" exit
goto MENU

:CHOOSE_PYTHON
REM === Use pyenv-win to select a Python version and create .venv ==========
echo Searching for installed Python versions via pyenv...

for /f "delims=" %%V in ('pyenv versions --bare 2^>nul') do (
    if not "%%~V"=="" (
        set /a count+=1
        set "ver!count!=%%~V"
        for /f "delims=" %%P in ('pyenv prefix %%~V 2^>nul') do (
            set "exe!count!=%%~P\python.exe"
        )
    )
)

if "!count!"=="0" (
    echo No Python installations found.
    pause
    goto MENU
)

echo.
echo Select a Python version:
for /L %%I in (1,1,!count!) do (
    echo %%I. !ver%%I!
)

set /p pychoice="Enter the number of the Python version to use: "
if not defined pychoice (
    echo Invalid selection.
    pause
	endlocal
    goto MENU
)

set "PY_SELECTED=!ver%pychoice%!"
set "PYTHON_EXE=!exe%pychoice%!"

REM Fallback: if path missing, resolve via pyenv which
if not exist "!PYTHON_EXE!" (
    call pyenv shell "!PY_SELECTED!" >nul 2>&1
    for /f "delims=" %%W in ('pyenv which python 2^>nul') do set "PYTHON_EXE=%%W"
)

if not exist "!PYTHON_EXE!" (
    echo [ERROR] Could not resolve python.exe for "!PY_SELECTED!".
    pause
    endlocal
    goto MENU
)

echo.
echo Creating virtual environment with "!PYTHON_EXE!" ...
"!PYTHON_EXE!" -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
) else (
    echo Virtual environment created at "%CD%\.venv"
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
echo pip version updated.


pause
goto MENU


:EXPORT_REQUIREMENTS
echo Exporting requirements.txt...
if not exist .venv\Scripts\activate (
    echo No virtual environment found. Please run option 1 first.
    pause
    goto MENU
)

call .venv\Scripts\activate.bat

pip freeze > requirements.txt
echo requirements.txt exported...
pause
goto MENU


:INSTALL_REQUIREMENTS
if not exist requirements.txt (
    echo requirements.txt not found.
    pause
    goto MENU
)

if not exist .venv\Scripts\activate (
    echo No virtual environment found. Please run option 1 first.
    pause
    goto MENU
)

call .venv\Scripts\activate.bat
echo Installing from requirements.txt...
pip install -r requirements.txt

echo Installation complete.
pause
goto MENU
