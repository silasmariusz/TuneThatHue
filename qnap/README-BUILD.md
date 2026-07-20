# Building the TuneThatHue .qpkg

The package ships a **portable CPython** (from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
runs on glibc ≥ 2.17 — QNAP's 2.21 qualifies) with the daemon's dependencies
baked in, so nothing needs installing on the NAS. The daemon runs under a
cron-based watchdog and serves its WebUI (pairing + settings) behind the QNAP
app-proxy.

## Layout (this dir)

```
qnap/
├── qpkg.cfg                         # QDK package config
├── build.sh                         # chmod + qbuild --7zip
├── build_sign.csv                   # files to sign
├── shared/
│   ├── tunethathue.sh               # QPKG service program (lifecycle + cron watchdog)
│   ├── etc/tunethathue/daemon.sh    # daemon controller (start/stop/watchdog)
│   ├── app/                         # the TuneThatHue code (assembled, gitignored)
│   └── runtime/python-<arch>/       # portable CPython + deps (assembled, gitignored)
└── icons/
```

`shared/app/`, `shared/runtime/`, the build output, and the signing keys
(`ca_certs` / `certificate` / `private_key`) are **assembled at build time and
git-ignored** — never commit the runtime blobs or the keys.

## Build (on a QNAP NAS with the QDK / `qbuild`)

```sh
# 1) app code -> shared/app  (from a checkout of this repo)
mkdir -p shared/app
cp -a ../effects ../python ../pystub ../resources ../config shared/app/

# 2) portable runtime + deps -> shared/runtime/python-x86_64
#    (repeat per arch: aarch64, armv7)
D=shared/runtime/python-x86_64 ; mkdir -p "$D" ; cd "$D"
curl -sL -o py.tgz https://github.com/astral-sh/python-build-standalone/releases/download/<TAG>/cpython-3.14.x+<TAG>-x86_64-unknown-linux-gnu-install_only.tar.gz
tar xzf py.tgz && rm py.tgz
TMPDIR="$PWD/_t" PIP_CACHE_DIR="$PWD/_c" ./python/bin/python3 -m pip install \
    aiosendspin==6.1.0 hue-entertainment cryptography
#    numpy is optional: it has no glibc≤2.21 wheel, so raw-PCM analysis is off
#    until a compatible numpy wheel is dropped in. WebUI/pairing/Hue output work
#    without it. (Build a manylinux2014 numpy wheel to enable it.)
cd -

# 3) signing keys (from a trusted local source; NEVER commit these)
cp /path/to/ca_certs /path/to/certificate /path/to/private_key .

# 4) build
sh build.sh          # -> build/TuneThatHue_0.1.0.qpkg
```

## Multi-arch

Repeat step 2 for `aarch64` (`aarch64-unknown-linux-gnu`) and `armv7`
(`armv7-unknown-linux-gnueabihf`). `daemon.sh` picks
`runtime/python-<arch>/` at runtime via `uname -m`.
