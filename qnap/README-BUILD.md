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

## NumPy wheel (the tricky bit)

The raw-PCM feature extractor needs NumPy. There is no NumPy wheel for Python
3.14 on QNAP's glibc (2.21) — modern NumPy wheels target glibc 2.28, and building
from source on the NAS fails (Entware gcc 8.4 < NumPy's required 10.3). Build it
in a `manylinux2014` container (glibc 2.17, ships cp314) on a box with **plain
Docker** (not QNAP Container Station):

```sh
# gcc in manylinux2014 is 10.2.1 (just under NumPy's 10.3 gate); the 10.2->10.3
# gap is a minor bugfix and irrelevant to our small FFT, so patch the check.
docker run --rm -v $PWD/wheels:/out quay.io/pypa/manylinux2014_x86_64 bash -c '
  PY=/opt/python/cp314-cp314/bin/python; cd /tmp
  URL=$($PY -c "import urllib.request,json;d=json.load(urllib.request.urlopen(\"https://pypi.org/pypi/numpy/2.5.1/json\"));print([u[\"url\"] for u in d[\"urls\"] if u[\"packagetype\"]==\"sdist\"][0])")
  curl -sL -o n.tgz "$URL" && tar xzf n.tgz && cd numpy-2.5.1/
  sed -i "s/>=10\.3/>=10.2/" meson.build
  source /opt/rh/devtoolset-10/enable
  $PY -m pip wheel . --config-settings=setup-args=-Dallow-noblas=true -w /out'
```

Then install the wheel into `shared/runtime/python-<arch>/`
(`python/bin/python3 -m pip install wheels/numpy-*.whl`). A glibc-2.17 build runs
fine on QNAP's 2.21 (glibc is forward-compatible). For aarch64/armv7 use the
matching `manylinux2014_aarch64` / `manylinux2014_armv7l` images (via qemu-binfmt).

## Multi-arch

Repeat step 2 for `aarch64` (`aarch64-unknown-linux-gnu`) and `armv7`
(`armv7-unknown-linux-gnueabihf`). `daemon.sh` picks
`runtime/python-<arch>/` at runtime via `uname -m`.
