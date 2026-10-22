# Building the TuneThatHue .qpkg

The package carries its own Python, a portable CPython from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) with the
daemon's dependencies installed, and an audio-only FFmpeg. Nothing is installed on the NAS
besides the package. The runtime is built for x86_64 only, so the package is offered for
x86_64 NAS models (App Center platform `TS-NASX86`).

How the package runs on the NAS (start, stop, the Enable switch, the watchdog and the panel
route) is in [`docs/qnap-service.md`](../docs/qnap-service.md).

## Layout (this folder)

```
qnap/
├── qpkg.cfg            QDK package config (name, version, ports, Proxy_Path /TuneThatHue/)
├── package_routines    install hooks and PKG_POST_REMOVE
├── build.sh            chmod + qbuild --7zip
├── build_sign.csv      the file QDK signs (tunethathue.sh)
├── build_ffmpeg.sh     builds the audio-only FFmpeg
├── icons/              TuneThatHue.gif, _80.gif, _gray.gif (PNG data), made by make_icons.py
└── shared/
    ├── tunethathue.sh                  the QTS service script (start, stop, restart, status, watchdog)
    ├── app/                            the TuneThatHue code (assembled, git-ignored)
    └── runtime/python-x86_64/          portable CPython + dependencies (assembled, git-ignored)
        runtime/ffmpeg-x86_64/ffmpeg    the audio-only FFmpeg (assembled, git-ignored)
```

`shared/app/`, `shared/runtime/`, `build/` and the signing files (`ca_certs`, `certificate`,
`private_key`) are git-ignored. Never commit the runtime or the keys.

## Build

QDK 2.5 (`qbuild`, with `7z` on the PATH for `--7zip`) runs on a NAS or on a Linux machine.

```sh
cd qnap

# 1) the app code
rm -rf shared/app && mkdir -p shared/app
cp -a ../effects ../python ../pystub ../resources ../config shared/app/
find shared/app -name __pycache__ -prune -exec rm -rf {} +

# 2) the runtime, if shared/runtime/ is not there yet
D=shared/runtime/python-x86_64 ; mkdir -p "$D" ; cd "$D"
curl -sL -o py.tgz https://github.com/astral-sh/python-build-standalone/releases/download/<TAG>/cpython-3.14.x+<TAG>-x86_64-unknown-linux-gnu-install_only.tar.gz
tar xzf py.tgz && rm py.tgz
TMPDIR="$PWD/_t" PIP_CACHE_DIR="$PWD/_c" ./python/bin/python3 -m pip install \
    aiohttp zeroconf aiosendspin==6.1.0 hue-entertainment cryptography wheels/numpy-*.whl
rm -rf _t _c ; cd -
# FFmpeg: ./build_ffmpeg.sh x86_64, then copy ffmpeg into shared/runtime/ffmpeg-x86_64/

# 3) the QNAP code-signing files next to qpkg.cfg (links are fine; never commit them)
ln -s /path/to/ca_certs /path/to/certificate /path/to/private_key .

# 4) build: build/TuneThatHue_<QPKG_VER>.qpkg, signed
sh build.sh
qbuild --verify-code-signing build/TuneThatHue_*.qpkg
```

Raise `QPKG_VER` in `qpkg.cfg` for every release. The qnaputilities.com feed sorts versions
number by number, so 0.9.10 comes after 0.9.9.

## NumPy wheel

The beat tracker and the feature extractor need NumPy. PyPI's NumPy wheels for Python 3.14
need glibc 2.28, so the runtime carries a NumPy built in a `manylinux2014` container
(glibc 2.17), which runs on every QTS 6.0 and QuTS hero 6.0 firmware. Build it on a machine
with plain Docker (not Container Station):

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

Then install the wheel into `shared/runtime/python-x86_64/`
(`python/bin/python3 -m pip install wheels/numpy-*.whl`).

## Other architectures

`tunethathue.sh` picks `runtime/python-<arch>/` by `uname -m` (`x86_64`, `aarch64`, `armv7`),
so an ARM package needs the same runtime built for that architecture
(`aarch64-unknown-linux-gnu`, `armv7-unknown-linux-gnueabihf`, the matching manylinux2014
images for NumPy) and its own App Center platform entry.
