#!/bin/sh
# Build the TuneThatHue .qpkg with the QNAP QDK (qbuild). Run on a QNAP NAS.
#
# Prereqs assembled into this directory by tools/qnap_assemble.sh (NOT committed):
#   shared/app/                     - the TuneThatHue Python code (effects/python/...)
#   shared/runtime/python-<arch>/   - portable CPython + installed deps, per arch
#   ca_certs certificate private_key build_sign.csv  - signing keys (gitignored)
#
# The payload is mirrored from a repo that may carry no Unix exec bit, so set +x
# on every script the app runs before qbuild packs them (qbuild preserves perms).

chmod 0755 shared/tunethathue.sh shared/etc/tunethathue/*.sh 2>/dev/null
chmod 0755 shared/runtime/python-*/python/bin/* 2>/dev/null

qbuild --7zip
