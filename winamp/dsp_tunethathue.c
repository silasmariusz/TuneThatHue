/*
 * dsp_tunethathue.c - Winamp DSP plugin that tees decoded PCM to the
 * TuneThatHue daemon as VBAN/UDP audio packets.
 *
 * TuneThatHue (c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
 * published: forum.qnap.net.pl   qnap app repo: myqnap.org
 *
 * Sound keeps playing normally through Winamp's own output; this plugin only
 * copies the samples to the network (best-effort UDP, never blocks playback).
 *
 * Config dialog: daemon IP + port + stream name, a "Test connection" button
 * (sends a PING datagram, waits for the daemon's PONG), and a live throughput
 * readout (KB/s + packets/s, refreshed once per second). Settings persist in
 * dsp_tunethathue.ini next to the DLL.
 *
 * Wire format: VBAN audio sub-protocol, PCM int16, so the daemon's one VBAN
 * receiver serves Winamp, Voicemeeter and foobar alike. A control datagram
 * "TTHP" (ping) / "TTHO" (pong) rides the same UDP port for the test button.
 *
 * Build (32-bit, Winamp is x86):
 *   windres dsp_tunethathue.rc -o rc.o
 *   i686-w64-mingw32-gcc -O2 -shared -static -o dsp_tunethathue.dll \
 *       dsp_tunethathue.c rc.o -lws2_32 -lcomctl32
 */

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "resource.h"

/* ---- Winamp DSP SDK ABI (stable public interface, declared inline) ---- */

typedef struct winampDSPModule {
    char *description;
    HWND hwndParent;
    HINSTANCE hDllInstance;
    void (*Config)(struct winampDSPModule *this_mod);
    int (*Init)(struct winampDSPModule *this_mod);
    int (*ModifySamples)(struct winampDSPModule *this_mod, short int *samples,
                         int numsamples, int bps, int nch, int srate);
    void (*Quit)(struct winampDSPModule *this_mod);
    void *userData;
} winampDSPModule;

typedef struct {
    int version; /* DSP_HDRVER = 0x20 */
    char *description;
    winampDSPModule *(*getModule)(int);
} winampDSPHeader;

#define DSP_HDRVER 0x20

/* ---- VBAN wire format (28-byte header + PCM payload) ---- */

#pragma pack(push, 1)
typedef struct {
    char vban[4];        /* 'V','B','A','N' */
    uint8_t format_SR;   /* bits 0-4: sample-rate index; bits 5-7: 0 = audio */
    uint8_t format_nbs;  /* sample frames per packet - 1 (max 256 frames)    */
    uint8_t format_nbc;  /* channels - 1                                      */
    uint8_t format_bit;  /* bits 0-2: 1 = int16 PCM; bits 4-7: 0 = plain PCM  */
    char streamname[16];
    uint32_t nuFrame;    /* running packet counter                            */
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

/* ---- plugin state ---- */

static HINSTANCE g_hinst;
static SOCKET g_sock = INVALID_SOCKET;
static struct sockaddr_in g_dest;
static volatile LONG g_ready = 0;
static uint32_t g_frame_counter = 0;
static char g_host[128] = "127.0.0.1";
static int g_port = 6980;
static char g_stream[17] = "Winamp1";
static char g_inipath[MAX_PATH];

/* Throughput counters (written on the audio thread, read by the dialog timer). */
static volatile LONG g_bytes_acc = 0;
static volatile LONG g_pkts_acc = 0;

static int sr_index(int srate)
{
    for (unsigned i = 0; i < VBAN_SR_COUNT; i++)
        if (VBAN_SR_TABLE[i] == srate)
            return (int)i;
    return -1;
}

static void resolve_dest(void)
{
    memset(&g_dest, 0, sizeof(g_dest));
    g_dest.sin_family = AF_INET;
    g_dest.sin_port = htons((u_short)g_port);
    g_dest.sin_addr.s_addr = inet_addr(g_host);
    if (g_dest.sin_addr.s_addr == INADDR_NONE) {
        struct addrinfo hints, *res = NULL;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        if (getaddrinfo(g_host, NULL, &hints, &res) == 0 && res) {
            g_dest.sin_addr = ((struct sockaddr_in *)res->ai_addr)->sin_addr;
            freeaddrinfo(res);
        }
    }
}

static void ini_path(void)
{
    GetModuleFileNameA(g_hinst, g_inipath, MAX_PATH);
    char *dot = strrchr(g_inipath, '.');
    if (dot)
        strcpy(dot, ".ini");
}

static void load_config(void)
{
    ini_path();
    if (GetFileAttributesA(g_inipath) == INVALID_FILE_ATTRIBUTES) {
        WritePrivateProfileStringA("tunethathue", "Host", g_host, g_inipath);
        WritePrivateProfileStringA("tunethathue", "Port", "6980", g_inipath);
        WritePrivateProfileStringA("tunethathue", "Stream", g_stream, g_inipath);
    }
    GetPrivateProfileStringA("tunethathue", "Host", g_host, g_host, sizeof(g_host), g_inipath);
    g_port = (int)GetPrivateProfileIntA("tunethathue", "Port", g_port, g_inipath);
    GetPrivateProfileStringA("tunethathue", "Stream", g_stream, g_stream, sizeof(g_stream), g_inipath);
}

static void save_config(void)
{
    char portbuf[16];
    _snprintf(portbuf, sizeof(portbuf), "%d", g_port);
    WritePrivateProfileStringA("tunethathue", "Host", g_host, g_inipath);
    WritePrivateProfileStringA("tunethathue", "Port", portbuf, g_inipath);
    WritePrivateProfileStringA("tunethathue", "Stream", g_stream, g_inipath);
}

static int open_socket(void)
{
    if (g_sock != INVALID_SOCKET)
        return 0;
    g_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (g_sock == INVALID_SOCKET)
        return 1;
    resolve_dest();
    return 0;
}

/*
 * Test connection: send a "TTHP" ping to host:port on a short-lived socket and
 * wait up to ~700 ms for the daemon's "TTHO" pong. Returns 1 on success.
 * Runs on the UI thread; the socket has a receive timeout so it never hangs.
 */
static int test_connection(const char *host, int port, char *detail, int detail_len)
{
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) {
        _snprintf(detail, detail_len, "socket() failed");
        return 0;
    }
    DWORD tmo = 700;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tmo, sizeof(tmo));

    struct sockaddr_in dst;
    memset(&dst, 0, sizeof(dst));
    dst.sin_family = AF_INET;
    dst.sin_port = htons((u_short)port);
    dst.sin_addr.s_addr = inet_addr(host);
    if (dst.sin_addr.s_addr == INADDR_NONE) {
        struct addrinfo hints, *res = NULL;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        if (getaddrinfo(host, NULL, &hints, &res) == 0 && res) {
            dst.sin_addr = ((struct sockaddr_in *)res->ai_addr)->sin_addr;
            freeaddrinfo(res);
        } else {
            _snprintf(detail, detail_len, "cannot resolve host");
            closesocket(s);
            return 0;
        }
    }

    const char ping[4] = {'T', 'T', 'H', 'P'};
    sendto(s, ping, 4, 0, (struct sockaddr *)&dst, sizeof(dst));

    char buf[32];
    struct sockaddr_in from;
    int fromlen = sizeof(from);
    int n = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fromlen);
    closesocket(s);

    if (n >= 4 && memcmp(buf, "TTHO", 4) == 0) {
        _snprintf(detail, detail_len, "daemon replied (%s:%d)", host, port);
        return 1;
    }
    _snprintf(detail, detail_len, "no reply from %s:%d (daemon running?)", host, port);
    return 0;
}

/* ---- config dialog ---- */

static INT_PTR CALLBACK config_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_INITDIALOG: {
        char portbuf[16];
        SetDlgItemTextA(dlg, IDC_HOST, g_host);
        _snprintf(portbuf, sizeof(portbuf), "%d", g_port);
        SetDlgItemTextA(dlg, IDC_PORT, portbuf);
        SetDlgItemTextA(dlg, IDC_STREAM, g_stream);
        SetDlgItemTextA(dlg, IDC_STATUS,
                        InterlockedCompareExchange(&g_ready, 0, 0) ? "sending" : "idle");
        SetTimer(dlg, 1, 1000, NULL); /* 1 Hz throughput refresh */
        return TRUE;
    }
    case WM_TIMER: {
        LONG bytes = InterlockedExchange(&g_bytes_acc, 0);
        LONG pkts = InterlockedExchange(&g_pkts_acc, 0);
        char buf[96];
        _snprintf(buf, sizeof(buf), "%.1f KB/s  (%ld pkt/s)", bytes / 1024.0, (long)pkts);
        SetDlgItemTextA(dlg, IDC_RATE, buf);
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_TEST: {
            char host[128], portbuf[16], detail[160];
            int port;
            GetDlgItemTextA(dlg, IDC_HOST, host, sizeof(host));
            GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
            port = atoi(portbuf);
            SetDlgItemTextA(dlg, IDC_STATUS, "testing...");
            UpdateWindow(dlg);
            int ok = test_connection(host, port, detail, sizeof(detail));
            char line[200];
            _snprintf(line, sizeof(line), "%s %s", ok ? "OK -" : "FAIL -", detail);
            SetDlgItemTextA(dlg, IDC_STATUS, line);
            return TRUE;
        }
        case IDOK: {
            char portbuf[16];
            GetDlgItemTextA(dlg, IDC_HOST, g_host, sizeof(g_host));
            GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
            g_port = atoi(portbuf);
            if (g_port <= 0 || g_port > 65535)
                g_port = 6980;
            GetDlgItemTextA(dlg, IDC_STREAM, g_stream, sizeof(g_stream));
            save_config();
            resolve_dest(); /* apply new destination live, no restart needed */
            KillTimer(dlg, 1);
            EndDialog(dlg, IDOK);
            return TRUE;
        }
        case IDCANCEL:
            KillTimer(dlg, 1);
            EndDialog(dlg, IDCANCEL);
            return TRUE;
        }
        break;
    case WM_CLOSE:
        KillTimer(dlg, 1);
        EndDialog(dlg, IDCANCEL);
        return TRUE;
    }
    return FALSE;
}

static void config(winampDSPModule *mod)
{
    DialogBoxParamA(g_hinst, MAKEINTRESOURCEA(IDD_CONFIG), mod->hwndParent, config_proc, 0);
}

static int init(winampDSPModule *mod)
{
    WSADATA wsa;
    (void)mod;
    load_config();
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
        return 1;
    if (open_socket() != 0)
        return 1;
    g_frame_counter = 0;
    InterlockedExchange(&g_ready, 1);
    return 0;
}

static void quit(winampDSPModule *mod)
{
    (void)mod;
    InterlockedExchange(&g_ready, 0);
    if (g_sock != INVALID_SOCKET) {
        closesocket(g_sock);
        g_sock = INVALID_SOCKET;
    }
    WSACleanup();
}

/*
 * Called on the playback thread with each decoded buffer (typically 576
 * frames). Convert to int16 if needed, slice into <=256-frame VBAN packets,
 * fire-and-forget over UDP, and return the buffer unmodified so playback is
 * untouched.
 */
static int modify_samples(winampDSPModule *mod, short int *samples,
                          int numsamples, int bps, int nch, int srate)
{
    (void)mod;
    if (!InterlockedCompareExchange(&g_ready, 1, 1) || numsamples <= 0 || nch <= 0 || nch > 8)
        return numsamples;

    int sri = sr_index(srate);
    if (sri < 0)
        return numsamples; /* exotic rate - hear it, just don't send it */

    static unsigned char pkt[sizeof(vban_header_t) + VBAN_MAX_FRAMES * 8 * 2];
    vban_header_t *hdr = (vban_header_t *)pkt;
    int16_t *payload = (int16_t *)(pkt + sizeof(vban_header_t));

    memcpy(hdr->vban, "VBAN", 4);
    hdr->format_SR = (uint8_t)sri;
    hdr->format_nbc = (uint8_t)(nch - 1);
    hdr->format_bit = VBAN_DATATYPE_INT16;
    memset(hdr->streamname, 0, sizeof(hdr->streamname));
    strncpy(hdr->streamname, g_stream, sizeof(hdr->streamname));

    int done = 0;
    while (done < numsamples) {
        int frames = numsamples - done;
        if (frames > VBAN_MAX_FRAMES)
            frames = VBAN_MAX_FRAMES;
        int count = frames * nch;

        if (bps == 16) {
            memcpy(payload, samples + (size_t)done * nch, (size_t)count * 2);
        } else if (bps == 24) {
            const unsigned char *src =
                (const unsigned char *)samples + (size_t)done * nch * 3;
            for (int i = 0; i < count; i++)
                payload[i] = (int16_t)((src[i * 3 + 2] << 8) | src[i * 3 + 1]);
        } else if (bps == 32) {
            const int32_t *src = (const int32_t *)samples + (size_t)done * nch;
            for (int i = 0; i < count; i++)
                payload[i] = (int16_t)(src[i] >> 16);
        } else {
            return numsamples; /* unknown depth - skip sending */
        }

        hdr->format_nbs = (uint8_t)(frames - 1);
        hdr->nuFrame = g_frame_counter++;
        int len = (int)(sizeof(vban_header_t) + (size_t)count * 2);
        sendto(g_sock, (const char *)pkt, len, 0,
               (const struct sockaddr *)&g_dest, sizeof(g_dest));
        InterlockedExchangeAdd(&g_bytes_acc, len);
        InterlockedIncrement(&g_pkts_acc);
        done += frames;
    }
    return numsamples;
}

/* ---- module plumbing ---- */

static winampDSPModule g_module = {
    "TuneThatHue PCM sender v0.2",
    NULL, NULL, config, init, modify_samples, quit, NULL,
};

static winampDSPModule *get_module(int which)
{
    return which == 0 ? &g_module : NULL;
}

static winampDSPHeader g_header = {
    DSP_HDRVER,
    "TuneThatHue (send audio to daemon)",
    get_module,
};

__declspec(dllexport) winampDSPHeader *winampDSPGetHeader2(void)
{
    return &g_header;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_hinst = hinst;
        DisableThreadLibraryCalls(hinst);
    }
    return TRUE;
}
