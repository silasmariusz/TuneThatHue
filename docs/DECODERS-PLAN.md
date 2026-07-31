# Playing anything that arrives

The four network inputs each accept only uncompressed audio today, which means every one
of them carries a footnote: *set the sender's output codec to wav*. A footnote is a
defect. Whoever installs this should be able to point any player at the box and have it
work, without being told to go and change a setting they have no reason to understand.

So: decode everything these protocols can deliver.

## What can actually arrive

Read out of each protocol's own options rather than guessed:

| Input | What the other end can send | Where that comes from |
| --- | --- | --- |
| Snapcast | `pcm`, `flac`, `ogg` (Vorbis), `opus` | the server's transport-codec setting; the client has no say |
| Squeezebox | `flac`, `mp3`, `aac`, `wav` | Music Assistant's output-codec setting; a real LMS transcodes to whatever the player claims in HELO |
| DLNA | `flac`, `mp3`, `aac`, `wav` from Music Assistant; anything at all from a phone or hi-fi app - ALAC, WMA, AC3 turn up in the wild | the same output-codec setting, and other control points do as they please |
| Sendspin | nothing - it sends extracted features, not audio | |

That is the list: **FLAC, MP3, AAC, Vorbis, Opus, ALAC, WMA, AC3 and the PCM family.**

## How

One decoder for all of it: **ffmpeg, fed through a pipe.** The alternative - a Python
library per format - means five dependencies, five wheels per architecture, and five sets
of edge cases in files written by other people's encoders. ffmpeg is the thing every
media player on the planet already relies on for exactly this.

    encoded bytes ──stdin──► ffmpeg ──stdout──► s16le 48 kHz stereo ──► the same
                                                                        on_chunk every
                                                                        input already uses

Decoding to a fixed 48 kHz stereo s16le is deliberate. The extractor wants int16, and
pinning the rate also removes the sample-rate churn that used to rebuild the extractor
whenever a source changed.

### Which ffmpeg

**Ours, bundled.** The NAS has one at `/usr/bin/ffmpeg`, and it is not good enough:

- it is **3.3.6 from 2017** and has **no AAC decoder at all** - one of the four codecs
  Music Assistant offers;
- it belongs to Multimedia Console, a package the user may never install or may remove;
- it is configured `--enable-gpl --enable-nonfree`.

So we build our own: audio decoders only, no video, no network, no external libraries,
statically linked, **LGPL** - the project is Apache-2.0 and a GPL binary would drag the
whole package into GPL, while none of the decoders we need are GPL-only. Built per
architecture on the compile box.

Lookup order at runtime: our bundled binary, then `/usr/bin/ffmpeg`, then `PATH`. With
none of them, the WAV path still works exactly as it does now and the panel says why the
rest does not.

## Tasks

1. **Build ffmpeg** - audio-only LGPL static, `x86_64` natively on the compile box, then
   `aarch64` and `armv7` cross. Measure the size; it has to be worth carrying.
2. **`python/decoder.py`** - spawn ffmpeg, pump the encoded stream in, read PCM out, hand
   it to the daemon. Handles: the process dying, the stream ending, a format even ffmpeg
   cannot read, and no ffmpeg at all.
3. **Wire it into the three audio inputs.** Each currently calls `feed_wav`; they get a
   decision instead: WAV goes down the existing path with no subprocess at all, anything
   else goes through ffmpeg.
4. **Say so in the panel** - which codec is playing, and if it cannot be decoded, why.
5. **Package it** - ship the binary per arch, resolve at runtime, and record the licence
   in THIRD-PARTY-NOTICES.
6. **Drop the footnotes** from the panel and the docs, because they stop being true.

## Test plan

Nothing counts unless it runs on the NAS from the installed package.

**Per codec, one file each** - FLAC, MP3, AAC in an ADTS stream, AAC in M4A, Ogg Vorbis,
Opus, ALAC, WMA, WAV - all generated from one known source: 30 seconds at a fixed tempo,
so every run is comparable.

1. **Decoder alone.** Each file in, s16le out. Check the sample count matches the source
   duration within a frame, the format is 48 kHz stereo, and the audio is not silence.
2. **Through each protocol.** Snapcast with the server on `flac`, `ogg`, `opus` and `pcm`;
   Squeezebox with `flac`, `mp3`, `aac`, `wav`; DLNA with the same four. For each: audio
   reaches the engine, spectra arrive at 20 Hz, and **the beat tracker locks to the known
   tempo** - that last one is what proves the samples are right and not noise.
3. **Ugly input.** A truncated file, a file that is not audio at all, a codec that is
   genuinely unsupported, ffmpeg killed mid-stream, and ffmpeg missing entirely. Each must
   leave the daemon running, the other inputs working, and a readable reason in the panel.
4. **Endurance.** An hour of continuous playback: no leak, no zombie ffmpeg, no drift.
5. **Regression.** WAV still plays with the decoder disabled, and the light wall still
   moves - checked by eye, not by counter.

## Results

Built and measured on the test NAS, from the packaged daemon, driven by the real other
end in every case. A run counts only if the beat tracker locks to the source's known
tempo - that is what proves the samples are music and not noise.

**The ffmpeg builds** are smaller than expected: x86_64 4.1 MB, aarch64 3.3 MB,
armv7 2.4 MB - audio decoders only, static, LGPL, no external libraries. Against an
80 MB package that is nothing, and the cross-builds that were supposed to be the risk
took minutes.

**Over DLNA, all ten formats play** - FLAC, MP3, AAC in ADTS, AAC in MP4, Ogg Vorbis,
Opus, ALAC, WMA, AC3 and WAV - each about 200 spectra per ten seconds and the tracker
locked at 119.7-119.8 BPM on a 120 BPM source.

**Over Squeezebox**: FLAC, MP3, AAC, Opus and WAV all lock at 119.7-120.2.

**Over Snapcast**: pcm, flac and ogg all lock.

### The two things that cannot work, and why

- **MP4/M4A through a one-way stream.** An MP4 keeps its index at the *end* of the file,
  so nothing can play it without seeking back - not us, not anything. Over DLNA it works,
  because there we hand ffmpeg the URL and it can range-request; over Squeezebox, where
  the server pushes a stream at us, it cannot. Reported in those words rather than as a
  demuxer error.
- **Opus over Snapcast.** Snapcast sends raw Opus packets with no container at all. FLAC
  chunks concatenate into a valid FLAC stream and Ogg chunks carry their own pages, but
  raw Opus packets would have to be re-framed first. Reported, with the three codecs that
  do work named.

### Two bugs the matrix found

- **Audio was delivered at disk speed.** A control point that hands us a file URL is read
  as fast as the disk allows, so thirty seconds of music reached the engine in one second:
  the beat tracker saw nonsense and the lights fired a burst and stopped. Now paced to the
  clock, which costs a live stream nothing because it never runs ahead.
- **Every Stop took two seconds**, because the teardown read ffmpeg's stderr while ffmpeg
  was still alive - waiting for an EOF that only arrives when it exits. Long enough for a
  UPnP control point to give up and call the device broken, which is why every second
  codec in the first matrix appeared to fail.

## Estimate

| | |
| --- | --- |
| ffmpeg builds | the x86_64 build is minutes; each cross-build is a toolchain install plus a compile. The arm ones carry the only real risk of the whole job. |
| decoder + wiring + panel | small - one new file and a branch in three places |
| the test corpus and the runs | the bulk of the work, and the point of it |
| package growth | measured after the first build, per architecture |

The risky part is not the decoding. It is the arm cross-builds and the honest test matrix -
nine codecs across three protocols is twenty-odd runs, and they only mean anything if each
one ends with the tempo locked.
