/*
 * foo_tunethathue.cpp - TuneThatHue DSP component for foobar2000.
 *
 * TuneThatHue (c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
 * published: forum.qnap.net.pl   qnap app repo: myqnap.org
 *
 * Tees the audio foobar2000 is playing to the TuneThatHue daemon as VBAN/UDP
 * int16 PCM - the exact same wire format the Winamp plugin and the SoundRecorder
 * app use, so one daemon receiver serves them all. Playback is untouched: every
 * chunk is passed through unchanged and only a converted copy goes to the
 * network (best-effort UDP, never blocks or stalls playback).
 *
 * Enable it in: Preferences -> Playback -> DSP Manager -> add "TuneThatHue".
 * Click "Configure selected" there for daemon IP / port / stream name and a
 * "Test connection" button. Settings live in the DSP preset, so foobar stores
 * them with the rest of its configuration.
 *
 * The config dialog is plain Win32 (no ATL/WTL) so the component builds with
 * the Build Tools C++ workload alone.
 *
 * NOTE: sending audio does not by itself make the lights beat-synced; the
 * daemon still has no beat detection. This component is the capture half only.
 */

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include "../foobar2000/SDK/foobar2000.h"
#include <stdint.h>
#include <stdio.h>
#include "resource.h"

#pragma comment(lib, "ws2_32.lib")

/* ---- VBAN wire format (28-byte header + PCM payload) ----
 * Identical to the SoundRecorder / Winamp plugin and to the daemon's parser. */
#pragma pack(push, 1)
typedef struct {
    char vban[4];        /* 'V','B','A','N' */
    uint8_t format_SR;   /* bits 0-4: sample-rate index; bits 5-7: 0 = audio */
    uint8_t format_nbs;  /* sample frames per packet - 1 (max 256 frames)    */
    uint8_t format_nbc;  /* channels - 1                                      */
    uint8_t format_bit;  /* bits 0-2: 1 = int16 PCM; bits 4-7: 0 = plain PCM  */
    char streamname[16];
    uint32_t nuFrame;
} vban_header_t;
#pragma pack(pop)

#define VBAN_MAX_FRAMES 256
#define VBAN_DATATYPE_INT16 0x01

static const long VBAN_SR_TABLE[] = {
    6000, 12000, 24000, 48000, 96000, 192000, 384000,
    8000, 16000, 32000, 64000, 128000, 256000, 512000,
    11025, 22050, 44100, 88200, 176400, 352800, 705600,
};
#define VBAN_SR_COUNT (sizeof(VBAN_SR_TABLE) / sizeof(VBAN_SR_TABLE[0]))

static int sr_index(int srate)
{
    for (unsigned i = 0; i < VBAN_SR_COUNT; i++)
        if (VBAN_SR_TABLE[i] == srate)
            return (int)i;
    return -1;
}

/* ---- component identity ---- */
DECLARE_COMPONENT_VERSION("TuneThatHue",
                          "1.0.0",
                          "TuneThatHue - sync Philips Hue lights to what foobar2000 is playing.\n"
                          "Sends the audio to the TuneThatHue daemon (VBAN/UDP); playback is not "
                          "altered.\n\n"
                          "(c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl\n"
                          "published: forum.qnap.net.pl   qnap app repo: myqnap.org");
VALIDATE_COMPONENT_FILENAME("foo_tunethathue.dll");

/* Our own GUID - identifies this DSP in foobar's chain configuration. */
// {6E1F2A54-9C3B-4D77-8A21-5B0E7C4D9A13}
static const GUID guid_tth_dsp = {
    0x6e1f2a54, 0x9c3b, 0x4d77, {0x8a, 0x21, 0x5b, 0xe, 0x7c, 0x4d, 0x9a, 0x13}};

/* ---- settings, carried inside the DSP preset ---- */
struct tth_settings {
    char host[128];
    int port;
    char stream[17];
};

static void settings_defaults(tth_settings &s)
{
    strcpy_s(s.host, sizeof(s.host), "127.0.0.1");
    s.port = 6980;
    strcpy_s(s.stream, sizeof(s.stream), "foobar2000");
}

static void make_preset(const tth_settings &s, dsp_preset &out)
{
    out.set_owner(guid_tth_dsp);
    out.set_data(&s, sizeof(s));
}

static tth_settings parse_preset(const dsp_preset &in)
{
    tth_settings s;
    settings_defaults(s);
    if (in.get_data_size() == sizeof(s))
        memcpy(&s, in.get_data(), sizeof(s));
    /* Never trust stored data blindly - keep the strings terminated. */
    s.host[sizeof(s.host) - 1] = 0;
    s.stream[sizeof(s.stream) - 1] = 0;
    if (s.port <= 0 || s.port > 65535)
        s.port = 6980;
    return s;
}

/* ---- UDP sender ---- */
static void resolve_dest(const char *host, int port, struct sockaddr_in &dest)
{
    memset(&dest, 0, sizeof(dest));
    dest.sin_family = AF_INET;
    dest.sin_port = htons((u_short)port);
    dest.sin_addr.s_addr = inet_addr(host);
    if (dest.sin_addr.s_addr == INADDR_NONE) {
        struct addrinfo hints, *res = NULL;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        if (getaddrinfo(host, NULL, &hints, &res) == 0 && res) {
            dest.sin_addr = ((struct sockaddr_in *)res->ai_addr)->sin_addr;
            freeaddrinfo(res);
        }
    }
}

/*
 * Test connection: send "TTHP", wait up to ~700ms for the daemon's "TTHO".
 * Returns 1 on success. Own short-lived socket with a receive timeout.
 */
static int test_connection(const char *host, int port, char *detail, int detail_len)
{
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) {
        _snprintf_s(detail, detail_len, _TRUNCATE, "socket() failed");
        return 0;
    }
    DWORD tmo = 700;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tmo, sizeof(tmo));

    struct sockaddr_in dst;
    resolve_dest(host, port, dst);
    if (dst.sin_addr.s_addr == INADDR_NONE || dst.sin_addr.s_addr == 0) {
        _snprintf_s(detail, detail_len, _TRUNCATE, "cannot resolve host");
        closesocket(s);
        return 0;
    }

    const char ping[4] = {'T', 'T', 'H', 'P'};
    sendto(s, ping, 4, 0, (struct sockaddr *)&dst, sizeof(dst));

    char buf[32];
    struct sockaddr_in from;
    int fromlen = sizeof(from);
    int n = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fromlen);
    closesocket(s);

    if (n >= 4 && memcmp(buf, "TTHO", 4) == 0) {
        _snprintf_s(detail, detail_len, _TRUNCATE, "daemon replied (%s:%d)", host, port);
        return 1;
    }
    _snprintf_s(detail, detail_len, _TRUNCATE, "no reply from %s:%d (daemon running?)", host, port);
    return 0;
}

/* ---- the DSP ---- */
class dsp_tunethathue : public dsp_impl_base {
public:
    dsp_tunethathue(dsp_preset const &in) : m_settings(parse_preset(in))
    {
        WSADATA wsa;
        WSAStartup(MAKEWORD(2, 2), &wsa);
        m_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        resolve_dest(m_settings.host, m_settings.port, m_dest);
    }

    ~dsp_tunethathue()
    {
        if (m_sock != INVALID_SOCKET)
            closesocket(m_sock);
        WSACleanup();
    }

    static GUID g_get_guid() { return guid_tth_dsp; }
    static void g_get_name(pfc::string_base &out) { out = "TuneThatHue (send audio to daemon)"; }

    /*
     * Copy the chunk out as int16 VBAN, then return true so foobar keeps the
     * original chunk: this is a pure tee, the audio itself is never altered.
     */
    bool on_chunk(audio_chunk *chunk, abort_callback &) override
    {
        if (m_sock == INVALID_SOCKET)
            return true;
        const unsigned nch = chunk->get_channels();
        const int sri = sr_index((int)chunk->get_sample_rate());
        if (sri < 0 || nch == 0 || nch > 8)
            return true; /* rate the wire format cannot express - just pass through */

        const audio_sample *src = chunk->get_data();
        size_t frames_total = chunk->get_sample_count();
        if (!src)
            return true;

        /* foobar hands us float samples in [-1,1]; the wire wants int16. */
        static int16_t conv[VBAN_MAX_FRAMES * 8];
        size_t done = 0;
        while (done < frames_total) {
            size_t frames = frames_total - done;
            if (frames > VBAN_MAX_FRAMES)
                frames = VBAN_MAX_FRAMES;
            const size_t count = frames * nch;
            for (size_t i = 0; i < count; i++) {
                audio_sample v = src[done * nch + i];
                if (v > 1.0)
                    v = 1.0;
                else if (v < -1.0)
                    v = -1.0;
                conv[i] = (int16_t)(v * 32767.0);
            }
            send_packet(conv, (int)frames, (int)nch, sri);
            done += frames;
        }
        return true;
    }

    void on_endofplayback(abort_callback &) override {}
    void on_endoftrack(abort_callback &) override {}
    void flush() override {}
    double get_latency() override { return 0; }
    bool need_track_change_mark() override { return false; }

    static bool g_get_default_preset(dsp_preset &out)
    {
        tth_settings s;
        settings_defaults(s);
        make_preset(s, out);
        return true;
    }
    static void g_show_config_popup(const dsp_preset &data, HWND parent,
                                    dsp_preset_edit_callback &callback);
    static bool g_have_config_popup() { return true; }

private:
    void send_packet(const int16_t *pcm, int frames, int nch, int sri)
    {
        unsigned char pkt[sizeof(vban_header_t) + VBAN_MAX_FRAMES * 8 * 2];
        vban_header_t *hdr = (vban_header_t *)pkt;
        memcpy(hdr->vban, "VBAN", 4);
        hdr->format_SR = (uint8_t)sri;
        hdr->format_nbs = (uint8_t)(frames - 1);
        hdr->format_nbc = (uint8_t)(nch - 1);
        hdr->format_bit = VBAN_DATATYPE_INT16;
        memset(hdr->streamname, 0, sizeof(hdr->streamname));
        strncpy_s(hdr->streamname, sizeof(hdr->streamname), m_settings.stream, _TRUNCATE);
        hdr->nuFrame = m_frame_counter++;
        const size_t payload = (size_t)frames * nch * 2;
        memcpy(pkt + sizeof(vban_header_t), pcm, payload);
        sendto(m_sock, (const char *)pkt, (int)(sizeof(vban_header_t) + payload), 0,
               (const struct sockaddr *)&m_dest, sizeof(m_dest));
    }

    tth_settings m_settings;
    SOCKET m_sock = INVALID_SOCKET;
    struct sockaddr_in m_dest {};
    uint32_t m_frame_counter = 0;
};

/* ---- config dialog (plain Win32; the SDK sample uses ATL, we avoid it) ---- */
struct dlg_ctx {
    tth_settings settings;
    dsp_preset_edit_callback *callback;
};

static INT_PTR CALLBACK config_dlg_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    dlg_ctx *ctx = (dlg_ctx *)GetWindowLongPtrA(dlg, GWLP_USERDATA);
    switch (msg) {
    case WM_INITDIALOG: {
        ctx = (dlg_ctx *)lp;
        SetWindowLongPtrA(dlg, GWLP_USERDATA, (LONG_PTR)ctx);
        char portbuf[16];
        SetDlgItemTextA(dlg, IDC_HOST, ctx->settings.host);
        _snprintf_s(portbuf, sizeof(portbuf), _TRUNCATE, "%d", ctx->settings.port);
        SetDlgItemTextA(dlg, IDC_PORT, portbuf);
        SetDlgItemTextA(dlg, IDC_STREAM, ctx->settings.stream);
        SetDlgItemTextA(dlg, IDC_STATUS, "add this DSP to the chain to start sending");
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_TEST: {
            char host[128], portbuf[16], detail[160], line[200];
            GetDlgItemTextA(dlg, IDC_HOST, host, sizeof(host));
            GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
            SetDlgItemTextA(dlg, IDC_STATUS, "testing...");
            UpdateWindow(dlg);
            int ok = test_connection(host, atoi(portbuf), detail, sizeof(detail));
            _snprintf_s(line, sizeof(line), _TRUNCATE, "%s %s", ok ? "OK -" : "FAIL -", detail);
            SetDlgItemTextA(dlg, IDC_STATUS, line);
            return TRUE;
        }
        case IDOK: {
            if (ctx) {
                char portbuf[16];
                GetDlgItemTextA(dlg, IDC_HOST, ctx->settings.host, sizeof(ctx->settings.host));
                GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
                ctx->settings.port = atoi(portbuf);
                if (ctx->settings.port <= 0 || ctx->settings.port > 65535)
                    ctx->settings.port = 6980;
                GetDlgItemTextA(dlg, IDC_STREAM, ctx->settings.stream,
                                sizeof(ctx->settings.stream));
                /* Hand the new settings back to foobar so they are stored and
                 * the running DSP instance is rebuilt with them. */
                dsp_preset_impl preset;
                make_preset(ctx->settings, preset);
                ctx->callback->on_preset_changed(preset);
            }
            EndDialog(dlg, IDOK);
            return TRUE;
        }
        case IDCANCEL:
            EndDialog(dlg, IDCANCEL);
            return TRUE;
        }
        break;
    case WM_CLOSE:
        EndDialog(dlg, IDCANCEL);
        return TRUE;
    }
    return FALSE;
}

void dsp_tunethathue::g_show_config_popup(const dsp_preset &data, HWND parent,
                                          dsp_preset_edit_callback &callback)
{
    dlg_ctx ctx;
    ctx.settings = parse_preset(data);
    ctx.callback = &callback;
    DialogBoxParamA(core_api::get_my_instance(), MAKEINTRESOURCEA(IDD_CONFIG), parent,
                    config_dlg_proc, (LPARAM)&ctx);
}

static dsp_factory_t<dsp_tunethathue> g_dsp_tunethathue_factory;
