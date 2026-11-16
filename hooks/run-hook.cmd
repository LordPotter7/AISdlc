:; # ---------------------------------------------------------------------------
:; # Polyglot launcher: cmd.exe reads the batch branch below, every POSIX shell
:; # reads these `:;`-prefixed lines. A line starting with `:` is a label to
:; # cmd.exe and is skipped; to a shell `:` is the no-op builtin. The `exec` on
:; # the last shell line replaces the process, so the batch branch is never
:; # parsed by a shell.
:; #
:; # Usage: run-hook.cmd <target> [args...]   where <target> is a sibling script.
:; # Targets are deliberately EXTENSIONLESS: Claude Code auto-detection on Windows
:; # prepends `bash` to a `.sh` command string, which would re-enter this launcher
:; # instead of running the target.
:; # ---------------------------------------------------------------------------
:; HOOK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
:; HOOK_TARGET="$1"
:; [ -n "$HOOK_TARGET" ] || exit 0
:; shift
:; [ -f "$HOOK_DIR/$HOOK_TARGET" ] || exit 0
:; exec sh "$HOOK_DIR/$HOOK_TARGET" "$@"
@echo off
setlocal enabledelayedexpansion
set "HOOK_DIR=%~dp0"
set "HOOK_TARGET=%~1"
if "%HOOK_TARGET%"=="" exit /b 0
if not exist "%HOOK_DIR%%HOOK_TARGET%" exit /b 0

rem Git for Windows ships bash but does not put it on PATH by default. Probe the
rem two standard install locations before falling back to PATH.
set "HOOK_BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "HOOK_BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined HOOK_BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "HOOK_BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined HOOK_BASH for %%I in (bash.exe) do if not "%%~$PATH:I"=="" set "HOOK_BASH=%%~$PATH:I"

rem No shell at all: allow the tool call rather than blocking every edit on this
rem machine. The guard says so on stderr; see SECURITY.md and docs/decisions/0007 for why this fails open.
if not defined HOOK_BASH (
  echo [ai-sdlc] no POSIX shell found; lifecycle guards are inactive this session. 1>&2
  exit /b 0
)

"%HOOK_BASH%" "%HOOK_DIR%%HOOK_TARGET%" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%
