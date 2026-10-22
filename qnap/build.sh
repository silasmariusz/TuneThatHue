#!/bin/sh
##
##   ███████ ██   ██ ▄█████▄ ▄██████ ███████ ██   ██ ▄█████▄ ███████ ██   ██ ██   ██ ▄██████
##     ▐█▌   ██   ██ ██   ██ ██▄▄▄▄    ▐█▌   ██▄▄▄██ ██▄▄▄██   ▐█▌   ██▄▄▄██ ██   ██ ██▄▄▄▄
##     ▐█▌   ██   ██ ██   ██ ██▀▀▀▀    ▐█▌   ██▀▀▀██ ██▀▀▀██   ▐█▌   ██▀▀▀██ ██   ██ ██▀▀▀▀
##     ▐█▌   ▀█████▀ ██   ██ ▀██████   ▐█▌   ██   ██ ██   ██   ▐█▌   ██   ██ ▀█████▀ ▀██████
##
##   Copyright © 2008-2027, Silas Mariusz <silas@qnap.com.antispam>
##                          QNAP Club Polska
##
##   www.qnaputilities.com  forum.qnap.net.pl
##
##   LICENSE: Freeware
##
##   ┌─────────┬─────────────────────────────────────────────┐
##   │ File    │ qnap/build.sh                               │
##   ├─────────┼─────────────────────────────────────────────┤
##   │ Package │ TuneThatHue, QPKG TuneThatHue               │
##   ├─────────┼─────────────────────────────────────────────┤
##   │ Runs on │ the build machine (a NAS or Linux with QDK) │
##   └─────────┴─────────────────────────────────────────────┘
##
# Build the TuneThatHue .qpkg with QNAP's QDK (qbuild), on a NAS or on a Linux machine with QDK 2.5.
#
# Assembled into this folder first (README-BUILD.md), none of it committed:
#   shared/app/                   the TuneThatHue code (effects, python, pystub, resources, config)
#   shared/runtime/python-x86_64/ the portable CPython with the installed dependencies
#   shared/runtime/ffmpeg-x86_64/ the audio-only FFmpeg (build_ffmpeg.sh)
#   certificate private_key ca_certs  QNAP code-signing files (git-ignored); build_sign.csv names the signed file
#
# The payload may come from a checkout without the exec bit, so the scripts and the runtime's programs get +x
# before qbuild packs them (qbuild keeps the modes).

cd "$(dirname "$0")" || exit 1
chmod 0755 shared/tunethathue.sh
chmod 0755 shared/runtime/python-*/python/bin/* shared/runtime/ffmpeg-*/ffmpeg 2>/dev/null

qbuild --7zip
