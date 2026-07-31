#!/bin/bash
# Build a small, audio-only, LGPL ffmpeg for TuneThatHue.
#
# Audio-only because we decode and throw the samples at an analyzer: no video, no
# filters beyond resampling, no network (we feed it through a pipe). LGPL because the
# project is Apache-2.0 and a GPL binary would drag the whole package into GPL - none of
# the audio decoders we need are GPL-only, so there is nothing to gain by enabling it.
set -e
ARCH="${1:-x86_64}"
SRC=/srv/tth/ffmpeg-src
OUT=/srv/tth/ffmpeg-build/$ARCH
VER=7.1.1

mkdir -p "$SRC" "$OUT"
cd "$SRC"
[ -f ffmpeg-$VER.tar.xz ] || curl -sL -o ffmpeg-$VER.tar.xz "https://ffmpeg.org/releases/ffmpeg-$VER.tar.xz"
[ -d ffmpeg-$VER ] || tar xf ffmpeg-$VER.tar.xz
BUILD="$SRC/build-$ARCH"
rm -rf "$BUILD"; mkdir -p "$BUILD"; cd "$BUILD"

CROSS=""
case "$ARCH" in
  aarch64) CROSS="--enable-cross-compile --cross-prefix=aarch64-linux-gnu- --arch=aarch64 --target-os=linux" ;;
  armv7)   CROSS="--enable-cross-compile --cross-prefix=arm-linux-gnueabihf- --arch=arm --target-os=linux" ;;
esac

"$SRC/ffmpeg-$VER/configure" \
  --prefix="$OUT" $CROSS \
  --disable-everything --disable-autodetect --disable-network --disable-doc \
  --disable-debug --disable-programs --enable-ffmpeg --enable-small \
  --enable-swresample --enable-avformat --enable-avcodec --enable-avutil \
  --enable-decoder=flac,mp3,mp3float,aac,aac_latm,aac_fixed,vorbis,opus,alac,wmav1,wmav2,ac3,eac3,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_u8,pcm_f32le,pcm_f64le,pcm_alaw,pcm_mulaw \
  --enable-parser=flac,mpegaudio,aac,aac_latm,vorbis,opus,ac3 \
  --enable-demuxer=flac,mp3,aac,ogg,wav,w64,matroska,mov,asf,ac3,eac3,aiff,au,pcm_s16le,pcm_s16be,pcm_f32le,mpegts,adts,data \
  --enable-muxer=s16le,wav --enable-encoder=pcm_s16le \
  --enable-protocol=pipe,file \
  --enable-filter=aresample,aformat,anull,atrim,aselect \
  --extra-cflags="-Os" --extra-ldflags="-static" --pkg-config-flags="--static"
make -j"$(nproc)"
make install
strip "$OUT/bin/ffmpeg" 2>/dev/null || true
ls -l "$OUT/bin/ffmpeg"
file "$OUT/bin/ffmpeg"
