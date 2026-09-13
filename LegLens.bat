@echo off
REM ===========================================================================
REM  LegLens - Auditor Forense
REM
REM  Doble clic para abrir la interfaz. No hace falta abrir una terminal.
REM
REM  La primera vez instala lo que falte (tarda un poco). Despues abre directo.
REM  Si algo falla, la ventana se queda abierta con el error en pantalla en vez
REM  de cerrarse de golpe.
REM ===========================================================================

setlocal
cd /d "%~dp0"
title LegLens - Auditor Forense

echo.
echo   LegLens - Auditor Forense
echo   =========================
echo.

REM --- Buscar Python -------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo   [X] No se encontro Python en este equipo.
    echo.
    echo       Instalalo desde https://www.python.org/downloads/
    echo       IMPORTANTE: marca la casilla "Add Python to PATH" al instalar.
    echo.
    pause
    exit /b 1
)

echo   [1/3] Python encontrado.

REM --- Dependencias --------------------------------------------------------
%PY% -c "import customtkinter, pandas, pydantic, networkx" >nul 2>&1
if errorlevel 1 (
    echo   [2/3] Instalando dependencias, esto tarda un par de minutos...
    echo.
    %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto deps_failed
    %PY% -m pip install --quiet --disable-pip-version-check -r requirements-ui.txt
    if errorlevel 1 goto deps_failed
    echo.
    echo         Listo.
) else (
    echo   [2/3] Dependencias ya instaladas.
)

REM --- Arrancar ------------------------------------------------------------
echo   [3/3] Abriendo la interfaz...
echo.

%PY% -m ui.leglens_app
if errorlevel 1 goto app_failed

endlocal
exit /b 0

REM --- Errores -------------------------------------------------------------
:deps_failed
echo.
echo   [X] No se pudieron instalar las dependencias.
echo.
echo       Revisa que haya conexion a internet y vuelve a intentar.
echo       Si el problema sigue, corre esto y manda la salida:
echo           %PY% -m pip install -r requirements.txt
echo.
pause
exit /b 1

:app_failed
echo.
echo   [X] La interfaz se cerro con un error. El detalle esta arriba.
echo.
echo       Lo mas comun: falta GEMINI_API_KEY en el archivo .env.
echo       La interfaz tambien corre sin clave usando un estate ya generado.
echo.
pause
exit /b 1
