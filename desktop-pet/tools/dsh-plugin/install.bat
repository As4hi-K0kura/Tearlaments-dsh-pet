@echo off
rem ============================================================================
rem  一键把本插件装进 DSH（走 DSH 自带的插件管理器，与 UI「添加插件」同一套逻辑）
rem
rem  用法：双击本文件，或在命令行执行 install.bat
rem  装完立即生效：桌面 profile 支持热加载，不需要重启 DSH。
rem ============================================================================
setlocal

set "DSH_EXE=%LOCALAPPDATA%\Programs\DSH NEXT\DSH NEXT.exe"
set "DSH_CLI=%LOCALAPPDATA%\Programs\DSH NEXT\resources\app.asar\lib\plugin-cli.js"
set "DSH_HOME=%USERPROFILE%\.dsh"
set "ELECTRON_RUN_AS_NODE=1"

if not exist "%DSH_EXE%" (
  echo [x] 找不到 DSH：%DSH_EXE%
  echo     请先安装 DSH NEXT，或改本脚本里的路径。
  exit /b 1
)

rem 本目录的绝对路径（%~dp0 自带结尾反斜杠，转成 pnpm 认识的正斜杠形式）
set "PLUGIN_DIR=%~dp0"
set "PLUGIN_DIR=%PLUGIN_DIR:\=/%"
set "PLUGIN_DIR=%PLUGIN_DIR:~0,-1%"

echo [*] 正在安装：%PLUGIN_DIR%
"%DSH_EXE%" --expose-internals "%DSH_CLI%" desktop add "%PLUGIN_DIR%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo [x] 安装失败，退出码 %RC%
  exit /b %RC%
)

echo [*] 安装完成。插件会在 127.0.0.1:3080 起服务（被占用则顺延）。
echo [*] 用桌宠客户端加 --dsh 启动即可自动接入。
exit /b 0
