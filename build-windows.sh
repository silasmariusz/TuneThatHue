#!/usr/bin/env bash
#
# Build the TuneThatHue Windows front-ends:
#   winamp/dsp_tunethathue.dll  - Winamp DSP plugin (32-bit, Winamp is x86)
#   systray/tth_capture.exe     - system-tray WASAPI loopback capture (64-bit)
#
# Both send VBAN/UDP int16 PCM (+ TTHP/TTHO ping) to the TuneThatHue daemon.
#
# Toolchain: llvm-mingw (portable LLVM/clang mingw-w64, ships i686 + x86_64
# targets, windres, and a UCRT runtime). Download a release from
#   https://github.com/mstorsjo/llvm-mingw/releases
# and point LLVM_MINGW_BIN at its bin/ directory, e.g.:
#   LLVM_MINGW_BIN=/path/to/llvm-mingw-*/bin ./build-windows.sh
#
# CRITICAL: -static is required. Without it the exe/dll depends on the
# llvm-mingw runtime DLLs and won't start on a clean Windows box.
set -euo pipefail

BIN="${LLVM_MINGW_BIN:?set LLVM_MINGW_BIN to the llvm-mingw bin/ dir}"
CC32="$BIN/i686-w64-mingw32-clang"
CC64="$BIN/x86_64-w64-mingw32-clang"
RC="$BIN/llvm-windres"
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "== Winamp DSP plugin (32-bit) =="
cd "$ROOT/winamp"
"$RC" -F pe-i386 dsp_tunethathue.rc -o rc.o   # 32-bit resource object
"$CC32" -O2 -shared -static -o dsp_tunethathue.dll dsp_tunethathue.c rc.o \
    -lws2_32 -lcomctl32
rm -f rc.o
echo "  -> winamp/dsp_tunethathue.dll"

echo "== system-tray capture (64-bit) =="
cd "$ROOT/systray"
"$RC" -F pe-x86-64 tth_capture.rc -o rc.o     # 64-bit resource object
"$CC64" -O2 -mwindows -static -o tth_capture.exe tth_capture.c rc.o \
    -lws2_32 -lole32 -lshell32
rm -f rc.o
echo "  -> systray/tth_capture.exe"

echo "OK. Both front-ends built (static, no runtime-DLL dependency)."
