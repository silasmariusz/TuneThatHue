/*
 * tth_capture.c - TuneThatHue system-audio capture, system-tray app.
 *
 * TuneThatHue (c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
 * published: forum.qnap.net.pl   qnap app repo: myqnap.org
 *
 * Captures EVERYTHING Windows is playing (WASAPI loopback on the default
 * render endpoint - Winamp, foobar, a browser, Spotify, a game, anything)
 * and tees it to the TuneThatHue daemon as VBAN/UDP int16 PCM - the exact
 * same wire format the Winamp DSP plugin uses, so the daemon's one VBAN
 * receiver serves both. Sound keeps playing normally; this only copies the
 * mix to the network (best-effort UDP, never blocks audio).
 *
 * Lives in the system tray. Double-click the icon (or right-click ->
 * Configure) for a small window: daemon IP + port + stream name, a
 * "Test connection" button (TTHP ping -> waits for the daemon's TTHO pong),
 * and a live throughput readout (KB/s + packets/s, refreshed once a second).
 * Settings persist in tth_capture.ini next to the exe.
 *
 * Build (64-bit GUI exe, no console):
 *   llvm-windres tth_capture.rc -o rc.o
 *   x86_64-w64-mingw32-clang -O2 -mwindows -o tth_capture.exe \
 *       tth_capture.c rc.o -lws2_32 -lole32 -lshell32
 *   (32-bit works too: i686-w64-mingw32-clang, same flags.)
 */

#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <mmreg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "resource.h"

/* ---- COM GUIDs (declared inline so no -luuid dependency) ---- */
static const GUID TTH_CLSID_MMDeviceEnumerator =
    {0xBCDE0395, 0xE52F, 0x467C, {0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E}};
static const GUID TTH_IID_IMMDeviceEnumerator =
    {0xA95664D2, 0x9614, 0x4F35, {0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6}};
static const GUID TTH_IID_IAudioClient =
    {0x1CB9AD4C, 0xDBFA, 0x4C32, {0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2}};
static const GUID TTH_IID_IAudioCaptureClient =
    {0xC8ADBD64, 0xE71E, 0x48A0, {0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17}};
static const GUID TTH_KSDATAFORMAT_SUBTYPE_IEEE_FLOAT =
    {0x00000003, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};
static const GUID TTH_KSDATAFORMAT_SUBTYPE_PCM =
    {0x00000001, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};

/* ---- VBAN wire format (28-byte header + PCM payload) ---- */
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

/* ---- app state ---- */
#define WM_TRAY (WM_APP + 1)
#define TRAY_UID 1
#define IDM_CONFIG 40001
#define IDM_TEST 40002
#define IDM_EXIT 40003

static HINSTANCE g_hinst;
static HWND g_hwnd;                 /* hidden message window                 */
static NOTIFYICONDATAA g_nid;
static SOCKET g_sock = INVALID_SOCKET;
static struct sockaddr_in g_dest;
static volatile LONG g_ready = 0;
static volatile LONG g_running = 1;
static HANDLE g_thread;
static uint32_t g_frame_counter = 0;
static char g_host[128] = "127.0.0.1";
static int g_port = 6980;
static char g_stream[17] = "SystemAudio";
static char g_inipath[MAX_PATH];

static volatile LONG g_bytes_acc = 0;
static volatile LONG g_pkts_acc = 0;
static volatile LONG g_capturing = 0; /* 1 once WASAPI is streaming            */

#ifdef TTH_DEBUG
#define DBG(x)                                                       \
    do {                                                             \
        FILE *_f = fopen("tth_debug.log", "a");                      \
        if (_f) {                                                    \
            fprintf(_f, "[dbg] %s\n", x);                            \
            fclose(_f);                                              \
        }                                                            \
    } while (0)
#else
#define DBG(x)
#endif

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
 * Slice an int16 interleaved buffer into <=256-frame VBAN packets and
 * fire-and-forget over UDP. Best-effort: a dropped datagram just skips a
 * frame of light, never stalls capture.
 */
static void send_vban(const int16_t *pcm, int frames_total, int nch, int sri)
{
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
    while (done < frames_total) {
        int frames = frames_total - done;
        if (frames > VBAN_MAX_FRAMES)
            frames = VBAN_MAX_FRAMES;
        int count = frames * nch;
        memcpy(payload, pcm + (size_t)done * nch, (size_t)count * 2);
        hdr->format_nbs = (uint8_t)(frames - 1);
        hdr->nuFrame = g_frame_counter++;
        int len = (int)(sizeof(vban_header_t) + (size_t)count * 2);
        sendto(g_sock, (const char *)pkt, len, 0,
               (const struct sockaddr *)&g_dest, sizeof(g_dest));
        InterlockedExchangeAdd(&g_bytes_acc, len);
        InterlockedIncrement(&g_pkts_acc);
        done += frames;
    }
}

/* Convert one WASAPI buffer (float32 or int16, N channels) to int16 and send. */
static void process_buffer(const BYTE *data, UINT32 frames, int nch, int is_float, int sri)
{
    if (frames == 0)
        return;
    static int16_t conv[VBAN_MAX_FRAMES * 8 * 4];
    /* Cap per-call frames to our scratch buffer; WASAPI packets are small. */
    UINT32 max_frames = (UINT32)(sizeof(conv) / sizeof(int16_t)) / (nch > 0 ? nch : 1);
    if (frames > max_frames)
        frames = max_frames;

    if (is_float) {
        const float *src = (const float *)data;
        int n = (int)frames * nch;
        for (int i = 0; i < n; i++) {
            float v = src[i];
            if (v > 1.0f)
                v = 1.0f;
            else if (v < -1.0f)
                v = -1.0f;
            conv[i] = (int16_t)(v * 32767.0f);
        }
        send_vban(conv, (int)frames, nch, sri);
    } else {
        /* already int16 interleaved */
        send_vban((const int16_t *)data, (int)frames, nch, sri);
    }
}

/*
 * WASAPI loopback capture thread. Opens the default render endpoint in
 * loopback mode, pulls the mix, converts to int16, and streams VBAN. On any
 * failure it backs off and retries, so unplugging/replugging a device or
 * switching the default output recovers on its own.
 */
static DWORD WINAPI capture_thread(LPVOID arg)
{
    (void)arg;
    CoInitializeEx(NULL, COINIT_MULTITHREADED);

    while (InterlockedCompareExchange(&g_running, 1, 1)) {
        IMMDeviceEnumerator *devenum = NULL;
        IMMDevice *dev = NULL;
        IAudioClient *ac = NULL;
        IAudioCaptureClient *cap = NULL;
        WAVEFORMATEX *wfx = NULL;
        HRESULT hr;
        int sri = -1, nch = 2, is_float = 1;

        hr = CoCreateInstance(&TTH_CLSID_MMDeviceEnumerator, NULL, CLSCTX_ALL,
                              &TTH_IID_IMMDeviceEnumerator, (void **)&devenum);
        if (FAILED(hr))
            goto retry;
        hr = IMMDeviceEnumerator_GetDefaultAudioEndpoint(devenum, eRender, eConsole, &dev);
        if (FAILED(hr))
            goto retry;
        hr = IMMDevice_Activate(dev, &TTH_IID_IAudioClient, CLSCTX_ALL, NULL, (void **)&ac);
        if (FAILED(hr))
            goto retry;
        hr = IAudioClient_GetMixFormat(ac, &wfx);
        if (FAILED(hr) || !wfx)
            goto retry;

        nch = wfx->nChannels;
        sri = sr_index((int)wfx->nSamplesPerSec);
        if (wfx->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) {
            is_float = 1;
        } else if (wfx->wFormatTag == WAVE_FORMAT_PCM) {
            is_float = (wfx->wBitsPerSample == 32); /* rare */
            is_float = 0;
        } else if (wfx->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
            WAVEFORMATEXTENSIBLE *ext = (WAVEFORMATEXTENSIBLE *)wfx;
            if (IsEqualGUID(&ext->SubFormat, &TTH_KSDATAFORMAT_SUBTYPE_IEEE_FLOAT))
                is_float = 1;
            else if (IsEqualGUID(&ext->SubFormat, &TTH_KSDATAFORMAT_SUBTYPE_PCM))
                is_float = 0;
            else
                goto retry;
        } else {
            goto retry;
        }
        /* Only 16-bit int and 32-bit float are handled; anything else -> skip. */
        if (!is_float && wfx->wBitsPerSample != 16)
            goto retry;
        if (sri < 0)
            goto retry; /* mix rate not in the VBAN table (exotic) */

        hr = IAudioClient_Initialize(ac, AUDCLNT_SHAREMODE_SHARED,
                                     AUDCLNT_STREAMFLAGS_LOOPBACK,
                                     2000000 /* 200ms buffer, 100ns units */, 0, wfx, NULL);
        if (FAILED(hr))
            goto retry;
        hr = IAudioClient_GetService(ac, &TTH_IID_IAudioCaptureClient, (void **)&cap);
        if (FAILED(hr))
            goto retry;
        hr = IAudioClient_Start(ac);
        if (FAILED(hr))
            goto retry;

        InterlockedExchange(&g_capturing, 1);
#ifdef TTH_DEBUG
        {
            char b[128];
            _snprintf(b, sizeof(b), "capture STARTED rate=%d ch=%d float=%d sri=%d",
                      (int)wfx->nSamplesPerSec, nch, is_float, sri);
            DBG(b);
        }
#endif

        while (InterlockedCompareExchange(&g_running, 1, 1)) {
            Sleep(5);
            UINT32 packet = 0;
            if (FAILED(IAudioCaptureClient_GetNextPacketSize(cap, &packet)))
                break;
            while (packet > 0) {
                BYTE *data = NULL;
                UINT32 frames = 0;
                DWORD flags = 0;
                hr = IAudioCaptureClient_GetBuffer(cap, &data, &frames, &flags, NULL, NULL);
                if (FAILED(hr))
                    break;
                if (InterlockedCompareExchange(&g_ready, 1, 1) &&
                    !(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                    process_buffer(data, frames, nch, is_float, sri);
                IAudioCaptureClient_ReleaseBuffer(cap, frames);
                if (FAILED(IAudioCaptureClient_GetNextPacketSize(cap, &packet)))
                    break;
            }
        }

    retry:
#ifdef TTH_DEBUG
        {
            char b[64];
            _snprintf(b, sizeof(b), "capture retry (hr=0x%08lx)", (unsigned long)hr);
            DBG(b);
        }
#endif
        InterlockedExchange(&g_capturing, 0);
        if (wfx)
            CoTaskMemFree(wfx);
        if (cap)
            IAudioCaptureClient_Release(cap);
        if (ac) {
            IAudioClient_Stop(ac);
            IAudioClient_Release(ac);
        }
        if (dev)
            IMMDevice_Release(dev);
        if (devenum)
            IMMDeviceEnumerator_Release(devenum);
        if (InterlockedCompareExchange(&g_running, 1, 1))
            Sleep(1000); /* back off before re-opening the endpoint */
    }

    CoUninitialize();
    return 0;
}

/*
 * Test connection: send "TTHP", wait up to ~700ms for the daemon's "TTHO".
 * Returns 1 on success. Own short-lived socket with a receive timeout.
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

/* ---- config dialog (same fields as the Winamp plugin) ---- */
static INT_PTR CALLBACK config_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    (void)lp;
    switch (msg) {
    case WM_INITDIALOG: {
        char portbuf[16];
        SetDlgItemTextA(dlg, IDC_HOST, g_host);
        _snprintf(portbuf, sizeof(portbuf), "%d", g_port);
        SetDlgItemTextA(dlg, IDC_PORT, portbuf);
        SetDlgItemTextA(dlg, IDC_STREAM, g_stream);
        SetDlgItemTextA(dlg, IDC_STATUS,
                        InterlockedCompareExchange(&g_capturing, 0, 0) ? "capturing system audio"
                                                                       : "starting capture...");
        SetTimer(dlg, 1, 1000, NULL);
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
            char host[128], portbuf[16], detail[160], line[200];
            int port;
            GetDlgItemTextA(dlg, IDC_HOST, host, sizeof(host));
            GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
            port = atoi(portbuf);
            SetDlgItemTextA(dlg, IDC_STATUS, "testing...");
            UpdateWindow(dlg);
            int ok = test_connection(host, port, detail, sizeof(detail));
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
            resolve_dest(); /* apply new destination live, no restart */
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

static void show_config(void)
{
    static int open = 0;
    if (open)
        return;
    open = 1;
    DialogBoxParamA(g_hinst, MAKEINTRESOURCEA(IDD_CONFIG), g_hwnd, config_proc, 0);
    open = 0;
}

static void tray_menu(void)
{
    POINT pt;
    GetCursorPos(&pt);
    HMENU m = CreatePopupMenu();
    AppendMenuA(m, MF_STRING, IDM_CONFIG, "Configure...");
    AppendMenuA(m, MF_STRING, IDM_TEST, "Test connection");
    AppendMenuA(m, MF_SEPARATOR, 0, NULL);
    AppendMenuA(m, MF_STRING, IDM_EXIT, "Exit");
    SetForegroundWindow(g_hwnd); /* so the menu closes on click-away */
    TrackPopupMenu(m, TPM_RIGHTBUTTON, pt.x, pt.y, 0, g_hwnd, NULL);
    DestroyMenu(m);
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_TRAY:
        if (LOWORD(lp) == WM_LBUTTONDBLCLK)
            show_config();
        else if (LOWORD(lp) == WM_RBUTTONUP)
            tray_menu();
        return 0;
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDM_CONFIG:
            show_config();
            return 0;
        case IDM_TEST: {
            char detail[160], line[220];
            int ok = test_connection(g_host, g_port, detail, sizeof(detail));
            _snprintf(line, sizeof(line), "%s %s", ok ? "Connected -" : "No daemon -", detail);
            g_nid.uFlags = NIF_INFO;
            strcpy(g_nid.szInfoTitle, "TuneThatHue");
            strncpy(g_nid.szInfo, line, sizeof(g_nid.szInfo) - 1);
            g_nid.dwInfoFlags = ok ? NIIF_INFO : NIIF_WARNING;
            Shell_NotifyIconA(NIM_MODIFY, &g_nid);
            g_nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
            return 0;
        }
        case IDM_EXIT:
            DestroyWindow(hwnd);
            return 0;
        }
        break;
    case WM_DESTROY:
        InterlockedExchange(&g_running, 0);
        Shell_NotifyIconA(NIM_DELETE, &g_nid);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

int WINAPI WinMain(HINSTANCE hinst, HINSTANCE prev, LPSTR cmdline, int show)
{
    (void)prev;
    (void)cmdline;
    (void)show;
    g_hinst = hinst;
    DBG("WINMAIN ENTERED (very first line)");

    /* single instance: if an instance already holds the tray icon, focus is
     * enough - a second copy exits. We look for our existing window rather
     * than a named mutex (a named mutex reported false positives under some
     * runtimes / left-over kernel objects). */
    HANDLE mtx = NULL;
    HWND existing = FindWindowA("TuneThatHueCaptureWnd", NULL);
    if (existing) {
        DBG("existing window found -> exit");
        return 0;
    }
    DBG("no existing instance ok");

    DBG("winmain start");
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
        return 1;
    DBG("wsastartup ok");
    load_config();
    if (open_socket() != 0)
        return 1;
    DBG("socket ok");
    InterlockedExchange(&g_ready, 1);

    WNDCLASSA wc;
    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = hinst;
    wc.lpszClassName = "TuneThatHueCaptureWnd";
    RegisterClassA(&wc);
    /* Hidden top-level tool window (never shown, kept out of the taskbar and
     * Alt-Tab). Top-level rather than message-only so a second instance can
     * find it by class name for the single-instance check above. */
    g_hwnd = CreateWindowExA(WS_EX_TOOLWINDOW, "TuneThatHueCaptureWnd", "TuneThatHue",
                             0, 0, 0, 0, 0, NULL, NULL, hinst, NULL);
    DBG("createwindow done");

    /* system-tray icon */
    memset(&g_nid, 0, sizeof(g_nid));
    g_nid.cbSize = sizeof(g_nid);
    g_nid.hWnd = g_hwnd;
    g_nid.uID = TRAY_UID;
    g_nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAY;
    HICON ico = LoadIconA(hinst, MAKEINTRESOURCEA(IDI_APPICON));
    g_nid.hIcon = ico ? ico : LoadIconA(NULL, (LPCSTR)IDI_APPLICATION);
    strcpy(g_nid.szTip, "TuneThatHue - system audio capture");
    Shell_NotifyIconA(NIM_ADD, &g_nid);
    DBG("tray icon added");

    g_thread = CreateThread(NULL, 0, capture_thread, NULL, 0, NULL);
    DBG("capture thread started; entering message loop");

    MSG msg;
    while (GetMessageA(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }

    InterlockedExchange(&g_running, 0);
    if (g_thread) {
        WaitForSingleObject(g_thread, 2000);
        CloseHandle(g_thread);
    }
    if (g_sock != INVALID_SOCKET)
        closesocket(g_sock);
    WSACleanup();
    if (mtx)
        CloseHandle(mtx);
    return (int)msg.wParam;
}
