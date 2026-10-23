@echo off
REM 一键启动桌宠（接入 DSH 宿主真实工作状态）
REM
REM 前提：DSH 里已装 dsh-pet-status 插件（见同目录 dsh-plugin/README.md）——
REM   插件在 DSH 宿主进程内聚合 session/event → 工作状态档位，
REM   并在 127.0.0.1:3080（被占则顺延）提供 dsh-pet 的 /state 长轮询契约。
REM 桌宠用 --dsh 自动发现 3080~3104 里的它，无需另外起任何进程。
REM
REM 用法：
REM   set PET_JDK=C:\path\to\jdk-21        REM 便携 JDK 21 目录（要含 bin\java.exe）
REM   set PET_LIB=C:\path\to\lib           REM 放 javafx-*-win.jar / gson / jna 的目录
REM   set PET_CLASSES=%CD%\..\target\classes
REM   start_pet_with_dsh.bat
REM
REM 不设 PET_CLASSES 时默认取本脚本上级目录下的 target\classes（先 mvn -q compile）。
REM 走 Maven 的话直接：mvn -q compile exec:java -Dexec.mainClass=app.pet.PetApp -Dexec.args=--dsh
setlocal
set JDK=%PET_JDK%
set LIB=%PET_LIB%
set CLASSES=%PET_CLASSES%
if "%CLASSES%"=="" set CLASSES=%~dp0..\target\classes

if "%JDK%"=="" (
  echo [start] 请先设置 PET_JDK 指向便携 JDK 21 目录，例如：
  echo        set PET_JDK=C:\path\to\jdk-21
  exit /b 1
)
if "%LIB%"=="" (
  echo [start] 请先设置 PET_LIB 指向依赖 jar 目录（javafx-*-win.jar / gson / jna），例如：
  echo        set PET_LIB=C:\path\to\lib
  exit /b 1
)

if not exist "%CLASSES%\app\pet\PetApp.class" (
  echo [start] 还没编译（缺 %CLASSES%\app\pet\PetApp.class），先编译：
  echo        cd %~dp0.. ^&^& mvn -q compile
  exit /b 1
)

set MP=%LIB%\javafx-base-21.0.4-win.jar;%LIB%\javafx-graphics-21.0.4-win.jar;%LIB%\javafx-controls-21.0.4-win.jar
set CP=%CLASSES%;%LIB%\gson-2.11.0.jar;%LIB%\jna-5.14.0.jar;%LIB%\jna-platform-5.14.0.jar
"%JDK%\bin\java.exe" -Dstdout.encoding=UTF-8 --module-path "%MP%" --add-modules javafx.controls -cp "%CP%" app.pet.PetApp --dsh
endlocal
