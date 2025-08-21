@echo off
setlocal ENABLEDELAYEDEXPANSION
title Podman Compose Menu

REM --- Detect podman-compose vs podman compose ---
where podman-compose >nul 2>&1
if %ERRORLEVEL%==0 (
  set "PCMD=podman-compose"
) else (
  REM Fallback to the built-in plugin syntax
  set "PCMD=podman compose"
)

REM --- Move to script directory (so compose picks up the right file) ---
cd /d "%~dp0"

:menu
cls
echo =============================================
echo            Podman Compose Menu
echo =============================================
echo [1] Start containers          (^%PCMD% start^)
echo [2] Stop containers           (^%PCMD% stop^)
echo [3] Up (detached)             (^%PCMD% up -d^)
echo [4] Down                      (^%PCMD% down^)
echo [5] Podman machine start      (podman machine start)
echo [6] Podman machine stop       (podman machine stop)
echo [7] Build images              (^%PCMD% build^)
echo [8] CLEAN / PURGE EVERYTHING  (containers, images, volumes, networks)
echo [9] Exit
echo.
set /p choice=Choose an option (1-9): 

if "%choice%"=="1" goto start_containers
if "%choice%"=="2" goto stop_containers
if "%choice%"=="3" goto up_containers
if "%choice%"=="4" goto down_containers
if "%choice%"=="5" goto machine_start
if "%choice%"=="6" goto machine_stop
if "%choice%"=="7" goto build_images
if "%choice%"=="8" goto purge_all
if "%choice%"=="9" goto end

echo Invalid choice. Press any key to try again...
pause >nul
goto menu

:start_containers
echo Running: %PCMD% start
%PCMD% start
goto done

:stop_containers
echo Running: %PCMD% stop
%PCMD% stop
goto done

:up_containers
echo Running: %PCMD% up -d
%PCMD% up -d
goto done

:down_containers
echo Running: %PCMD% down
%PCMD% down
goto done

:machine_start
echo Running: podman machine start
podman machine start
goto done

:machine_stop
echo Running: podman machine stop
podman machine stop
goto done

:build_images
echo Running: %PCMD% up -d --build
%PCMD% up -d --build
goto done

:purge_all
echo.
echo WARNING: This will DELETE ALL Podman containers, images, volumes,
echo and ALL NON-DEFAULT networks on this machine.
echo Default network "podman" will be kept.
echo.
set /p AREYOUSURE=Type YES to proceed (or anything else to cancel): 
if /I not "%AREYOUSURE%"=="YES" (
  echo Cancelled.
  goto done
)

echo Stopping and removing ALL containers...
for /f %%i in ('podman ps -aq') do podman rm -f %%i

echo Removing ALL images...
for /f %%i in ('podman images -aq') do podman rmi -f %%i

echo Removing ALL volumes...
for /f %%i in ('podman volume ls -q') do podman volume rm -f %%i

echo Removing ALL non-default networks...
for /f %%N in ('podman network ls --format "{{.Name}}"') do @if /I not "%%N"=="podman" podman network rm -f "%%N"

echo Deep prune (leftovers)...
podman system prune -a -f --volumes
podman network prune -f

echo.
echo PURGE COMPLETE.
goto done

:done
echo.
pause
goto menu

:end
echo Bye!
endlocal
