/*
 * tth_soundrecorder.cpp - TuneThatHue SoundRecorder (Windows system-tray app).
 *
 * TuneThatHue (c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
 * published: forum.qnap.net.pl   qnap app repo: myqnap.org
 *
 * Captures Windows audio and tees it to the TuneThatHue daemon as VBAN/UDP
 * int16 PCM - the exact same wire format the Winamp and foobar2000 plugins
 * use, so the daemon's one VBAN receiver serves them all. Sound keeps playing
 * normally; this only copies the audio to the network (best-effort UDP, never
 * blocks playback).
 *
 * Four capture sources, selectable in the config dialog:
 *   1. Default output (loopback)  - whatever Windows is playing, on the default
 *                                   playback device. Covers DirectSound/XAudio2/
 *                                   DirectX, browsers, games, every player, since
 *                                   they all render through the shared mixer.
 *   2. Specific output (loopback) - same, but on a chosen playback device (second
 *                                   sound card, HDMI, virtual cable).
 *   3. Input device               - a recording endpoint: Stereo Mix, line-in, mic.
 *   4. Specific application       - WASAPI process loopback: only that app's audio
 *                                   (Windows 10 2004 / build 20348 and newer).
 *
 * KNOWN LIMIT: an app that opens the output in WASAPI *exclusive* mode bypasses
 * the Windows mixer, so loopback on that device records silence. That cannot be
 * intercepted - use another output device, or capture the app directly (source 4).
 *
 * NOTE: sending audio does not by itself make the lights beat-synced; the daemon
 * still has no beat detection. This app is the capture half only.
 *
 * Build (64-bit GUI exe, MSVC - see build-windows.ps1):
 *   rc.exe /fo tth_soundrecorder.res tth_soundrecorder.rc
 *   cl /O2 /EHsc /MT tth_soundrecorder.cpp tth_soundrecorder.res /link
 *      /SUBSYSTEM:WINDOWS /MACHINE:X64 ws2_32.lib ole32.lib oleaut32.lib
 *      shell32.lib mmdevapi.lib runtimeobject.lib
 */

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <audiopolicy.h>
#include <functiondiscoverykeys_devpkey.h>
#include <mmreg.h>
#include <tlhelp32.h>
#include <wrl/implements.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vector>
#include <string>
#include "resource.h"

using Microsoft::WRL::ComPtr;

/* ---- VBAN wire format (28-byte header + PCM payload) ----
 * Identical to the Winamp/foobar plugins and to the daemon's parser; do not
 * change without changing all four. */
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

/* ---- capture sources ---- */
enum tth_source {
    SRC_DEFAULT_LOOPBACK = 0, /* default playback device, loopback            */
    SRC_DEVICE_LOOPBACK  = 1, /* chosen playback device, loopback             */
    SRC_INPUT_CAPTURE    = 2, /* chosen recording device (Stereo Mix/line-in) */
    SRC_PROCESS_LOOPBACK = 3, /* one application (Win10 2004+)                */
};

/* ---- app state ---- */
#define WM_TRAY (WM_APP + 1)
#define TRAY_UID 1
#define IDM_CONFIG 40001
#define IDM_TEST 40002
#define IDM_EXIT 40003

static HINSTANCE g_hinst;
static HWND g_hwnd;
static NOTIFYICONDATAA g_nid;
static SOCKET g_sock = INVALID_SOCKET;
static struct sockaddr_in g_dest;
static volatile LONG g_ready = 0;
static volatile LONG g_running = 1;
static volatile LONG g_reopen = 0;   /* set when the source changes: reopen live */
static HANDLE g_thread;
static uint32_t g_frame_counter = 0;
static char g_host[128] = "127.0.0.1";
static int g_port = 6980;
static char g_stream[17] = "SystemAudio";
static char g_inipath[MAX_PATH];

/* selected source (persisted) */
static volatile LONG g_source = SRC_DEFAULT_LOOPBACK;
static CRITICAL_SECTION g_target_lock;   /* guards g_device_id / g_process_name */
static std::wstring g_device_id;         /* IMMDevice::GetId() of the chosen endpoint */
static std::wstring g_process_name;      /* exe name for process loopback */

static volatile LONG g_bytes_acc = 0;
static volatile LONG g_pkts_acc = 0;
static volatile LONG g_capturing = 0;
static char g_status[160] = "starting capture...";  /* what the dialog shows */

static void set_status(const char *s)
{
    EnterCriticalSection(&g_target_lock);
    strncpy_s(g_status, sizeof(g_status), s, _TRUNCATE);
    LeaveCriticalSection(&g_target_lock);
}

/* ---- small string helpers (the audio APIs are wide, our INI/UI are ANSI) ---- */
static std::string to_utf8(const std::wstring &w)
{
    if (w.empty())
        return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), NULL, 0, NULL, NULL);
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), &s[0], n, NULL, NULL);
    return s;
}

static std::wstring from_utf8(const std::string &s)
{
    if (s.empty())
        return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), NULL, 0);
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

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

/* ---- config (INI next to the exe, section [tunethathue]) ---- */
static void ini_path(void)
{
    GetModuleFileNameA(g_hinst, g_inipath, MAX_PATH);
    char *dot = strrchr(g_inipath, '.');
    if (dot)
        strcpy_s(dot, sizeof(g_inipath) - (dot - g_inipath), ".ini");
}

static void load_config(void)
{
    ini_path();
    if (GetFileAttributesA(g_inipath) == INVALID_FILE_ATTRIBUTES) {
        WritePrivateProfileStringA("tunethathue", "Host", g_host, g_inipath);
        WritePrivateProfileStringA("tunethathue", "Port", "6980", g_inipath);
        WritePrivateProfileStringA("tunethathue", "Stream", g_stream, g_inipath);
        WritePrivateProfileStringA("tunethathue", "Source", "0", g_inipath);
    }
    GetPrivateProfileStringA("tunethathue", "Host", g_host, g_host, sizeof(g_host), g_inipath);
    g_port = (int)GetPrivateProfileIntA("tunethathue", "Port", g_port, g_inipath);
    GetPrivateProfileStringA("tunethathue", "Stream", g_stream, g_stream, sizeof(g_stream), g_inipath);

    int src = (int)GetPrivateProfileIntA("tunethathue", "Source", SRC_DEFAULT_LOOPBACK, g_inipath);
    if (src < SRC_DEFAULT_LOOPBACK || src > SRC_PROCESS_LOOPBACK)
        src = SRC_DEFAULT_LOOPBACK;
    InterlockedExchange(&g_source, src);

    char buf[512];
    GetPrivateProfileStringA("tunethathue", "DeviceId", "", buf, sizeof(buf), g_inipath);
    g_device_id = from_utf8(buf);
    /* The process is stored by NAME, not PID: PIDs change every run, so we
     * re-resolve the name to a live PID each time capture (re)starts. */
    GetPrivateProfileStringA("tunethathue", "Process", "", buf, sizeof(buf), g_inipath);
    g_process_name = from_utf8(buf);
}

static void save_config(void)
{
    char portbuf[16], srcbuf[16];
    _snprintf_s(portbuf, sizeof(portbuf), _TRUNCATE, "%d", g_port);
    _snprintf_s(srcbuf, sizeof(srcbuf), _TRUNCATE, "%ld",
                InterlockedCompareExchange(&g_source, 0, 0));
    WritePrivateProfileStringA("tunethathue", "Host", g_host, g_inipath);
    WritePrivateProfileStringA("tunethathue", "Port", portbuf, g_inipath);
    WritePrivateProfileStringA("tunethathue", "Stream", g_stream, g_inipath);
    WritePrivateProfileStringA("tunethathue", "Source", srcbuf, g_inipath);
    EnterCriticalSection(&g_target_lock);
    std::string dev = to_utf8(g_device_id), proc = to_utf8(g_process_name);
    LeaveCriticalSection(&g_target_lock);
    WritePrivateProfileStringA("tunethathue", "DeviceId", dev.c_str(), g_inipath);
    WritePrivateProfileStringA("tunethathue", "Process", proc.c_str(), g_inipath);
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

/* ---- device / process enumeration (for the dialog dropdowns) ---- */
struct DeviceEntry {
    std::wstring id;
    std::string label;
};
struct ProcessEntry {
    DWORD pid;
    std::wstring exe;
    std::string label;
};

/* List active endpoints of one kind. flow = eRender (playback) or eCapture. */
static std::vector<DeviceEntry> enum_devices(EDataFlow flow)
{
    std::vector<DeviceEntry> out;
    ComPtr<IMMDeviceEnumerator> devenum;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL,
                                IID_PPV_ARGS(&devenum))))
        return out;
    ComPtr<IMMDeviceCollection> coll;
    if (FAILED(devenum->EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE, &coll)))
        return out;
    UINT count = 0;
    coll->GetCount(&count);
    for (UINT i = 0; i < count; i++) {
        ComPtr<IMMDevice> dev;
        if (FAILED(coll->Item(i, &dev)))
            continue;
        LPWSTR id = NULL;
        if (FAILED(dev->GetId(&id)) || !id)
            continue;
        DeviceEntry e;
        e.id = id;
        CoTaskMemFree(id);
        ComPtr<IPropertyStore> props;
        if (SUCCEEDED(dev->OpenPropertyStore(STGM_READ, &props))) {
            PROPVARIANT pv;
            PropVariantInit(&pv);
            if (SUCCEEDED(props->GetValue(PKEY_Device_FriendlyName, &pv)) &&
                pv.vt == VT_LPWSTR && pv.pwszVal)
                e.label = to_utf8(pv.pwszVal);
            PropVariantClear(&pv);
        }
        if (e.label.empty())
            e.label = "(unnamed device)";
        out.push_back(e);
    }
    return out;
}

/* Resolve a PID to its exe file name ("firefox.exe"). */
static std::wstring exe_name_of(DWORD pid)
{
    std::wstring name;
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (h) {
        wchar_t path[MAX_PATH];
        DWORD n = MAX_PATH;
        if (QueryFullProcessImageNameW(h, 0, path, &n)) {
            const wchar_t *slash = wcsrchr(path, L'\\');
            name = slash ? slash + 1 : path;
        }
        CloseHandle(h);
    }
    return name;
}

/*
 * Applications that currently have audio sessions on the default playback
 * device. That is a far more useful (and much shorter) list than every running
 * process, since only these actually produce sound. Falls back to a full
 * process snapshot when nothing is playing.
 */
static std::vector<ProcessEntry> enum_audio_processes(void)
{
    std::vector<ProcessEntry> out;
    ComPtr<IMMDeviceEnumerator> devenum;
    if (SUCCEEDED(CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL,
                                   IID_PPV_ARGS(&devenum)))) {
        ComPtr<IMMDevice> dev;
        if (SUCCEEDED(devenum->GetDefaultAudioEndpoint(eRender, eConsole, &dev))) {
            ComPtr<IAudioSessionManager2> mgr;
            if (SUCCEEDED(dev->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, NULL,
                                        (void **)mgr.GetAddressOf()))) {
                ComPtr<IAudioSessionEnumerator> sessions;
                if (SUCCEEDED(mgr->GetSessionEnumerator(&sessions))) {
                    int count = 0;
                    sessions->GetCount(&count);
                    for (int i = 0; i < count; i++) {
                        ComPtr<IAudioSessionControl> ctrl;
                        if (FAILED(sessions->GetSession(i, &ctrl)))
                            continue;
                        ComPtr<IAudioSessionControl2> ctrl2;
                        if (FAILED(ctrl.As(&ctrl2)))
                            continue;
                        DWORD pid = 0;
                        if (FAILED(ctrl2->GetProcessId(&pid)) || pid == 0)
                            continue;
                        if (ctrl2->IsSystemSoundsSession() == S_OK)
                            continue;
                        std::wstring exe = exe_name_of(pid);
                        if (exe.empty())
                            continue;
                        bool dup = false;
                        for (auto &p : out)
                            if (_wcsicmp(p.exe.c_str(), exe.c_str()) == 0)
                                dup = true;
                        if (dup)
                            continue;
                        ProcessEntry e;
                        e.pid = pid;
                        e.exe = exe;
                        e.label = to_utf8(exe) + " (playing)";
                        out.push_back(e);
                    }
                }
            }
        }
    }
    if (!out.empty())
        return out;

    /* Nothing is playing right now - offer the running processes instead. */
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap != INVALID_HANDLE_VALUE) {
        PROCESSENTRY32W pe;
        pe.dwSize = sizeof(pe);
        if (Process32FirstW(snap, &pe)) {
            do {
                ProcessEntry e;
                e.pid = pe.th32ProcessID;
                e.exe = pe.szExeFile;
                e.label = to_utf8(pe.szExeFile);
                out.push_back(e);
            } while (Process32NextW(snap, &pe));
        }
        CloseHandle(snap);
    }
    return out;
}

static DWORD pid_of_exe(const std::wstring &exe)
{
    DWORD pid = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap != INVALID_HANDLE_VALUE) {
        PROCESSENTRY32W pe;
        pe.dwSize = sizeof(pe);
        if (Process32FirstW(snap, &pe)) {
            do {
                if (_wcsicmp(pe.szExeFile, exe.c_str()) == 0) {
                    pid = pe.th32ProcessID;
                    break;
                }
            } while (Process32NextW(snap, &pe));
        }
        CloseHandle(snap);
    }
    return pid;
}

/* Process loopback needs Windows 10 2004 (build 19041) or newer. */
static bool process_loopback_supported(void)
{
    typedef LONG(WINAPI * RtlGetVersionPtr)(PRTL_OSVERSIONINFOW);
    HMODULE nt = GetModuleHandleW(L"ntdll.dll");
    if (!nt)
        return false;
    RtlGetVersionPtr p = (RtlGetVersionPtr)GetProcAddress(nt, "RtlGetVersion");
    if (!p)
        return false;
    RTL_OSVERSIONINFOW vi;
    memset(&vi, 0, sizeof(vi));
    vi.dwOSVersionInfoSize = sizeof(vi);
    if (p(&vi) != 0)
        return false;
    return vi.dwMajorVersion > 10 || (vi.dwMajorVersion == 10 && vi.dwBuildNumber >= 19041);
}

/*
 * ActivateAudioInterfaceAsync is asynchronous; this handler just signals an
 * event so the capture thread can wait for the IAudioClient. Same shape as
 * Microsoft's ApplicationLoopback SDK sample.
 */
class ActivationHandler
    : public Microsoft::WRL::RuntimeClass<
          Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom |
                                            Microsoft::WRL::InhibitRoOriginateError>,
          Microsoft::WRL::FtmBase, IActivateAudioInterfaceCompletionHandler> {
public:
    HANDLE done = CreateEventW(NULL, TRUE, FALSE, NULL);
    ~ActivationHandler()
    {
        if (done)
            CloseHandle(done);
    }
    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation *op) override
    {
        (void)op;
        SetEvent(done);
        return S_OK;
    }
};

/*
 * Open the audio client for the currently selected source. Returns the started
 * client + capture service and the format details the sender needs. On any
 * failure returns false and the caller retries after a back-off.
 */
static bool open_capture(ComPtr<IAudioClient> &ac, ComPtr<IAudioCaptureClient> &cap,
                         int *out_nch, int *out_is_float, int *out_sri, char *why, size_t why_len)
{
    LONG source = InterlockedCompareExchange(&g_source, 0, 0);
    EnterCriticalSection(&g_target_lock);
    std::wstring device_id = g_device_id;
    std::wstring process_name = g_process_name;
    LeaveCriticalSection(&g_target_lock);

    WAVEFORMATEX *mix = NULL;
    WAVEFORMATEX pcm16;
    const WAVEFORMATEX *fmt = NULL;
    DWORD flags = AUDCLNT_STREAMFLAGS_LOOPBACK;

    if (source == SRC_PROCESS_LOOPBACK) {
        if (!process_loopback_supported()) {
            _snprintf_s(why, why_len, _TRUNCATE, "app capture needs Windows 10 2004 or newer");
            return false;
        }
        if (process_name.empty()) {
            _snprintf_s(why, why_len, _TRUNCATE, "no application selected");
            return false;
        }
        DWORD pid = pid_of_exe(process_name);
        if (!pid) {
            _snprintf_s(why, why_len, _TRUNCATE, "waiting for %s to start",
                        to_utf8(process_name).c_str());
            return false;
        }

        AUDIOCLIENT_ACTIVATION_PARAMS params;
        memset(&params, 0, sizeof(params));
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        params.ProcessLoopbackParams.TargetProcessId = pid;
        params.ProcessLoopbackParams.ProcessLoopbackMode =
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
        PROPVARIANT pv;
        PropVariantInit(&pv);
        pv.vt = VT_BLOB;
        pv.blob.cbSize = sizeof(params);
        pv.blob.pBlobData = (BYTE *)&params;

        Microsoft::WRL::ComPtr<ActivationHandler> handler = Microsoft::WRL::Make<ActivationHandler>();
        ComPtr<IActivateAudioInterfaceAsyncOperation> op;
        HRESULT hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
                                                 __uuidof(IAudioClient), &pv, handler.Get(), &op);
        if (FAILED(hr)) {
            _snprintf_s(why, why_len, _TRUNCATE, "app capture unavailable (0x%08lx)",
                        (unsigned long)hr);
            return false;
        }
        if (WaitForSingleObject(handler->done, 3000) != WAIT_OBJECT_0) {
            _snprintf_s(why, why_len, _TRUNCATE, "app capture timed out");
            return false;
        }
        HRESULT act_hr = E_FAIL;
        ComPtr<IUnknown> unk;
        if (FAILED(op->GetActivateResult(&act_hr, &unk)) || FAILED(act_hr) || !unk) {
            _snprintf_s(why, why_len, _TRUNCATE, "app capture refused (0x%08lx)",
                        (unsigned long)act_hr);
            return false;
        }
        if (FAILED(unk.As(&ac))) {
            _snprintf_s(why, why_len, _TRUNCATE, "app capture: no audio client");
            return false;
        }

        /* For process loopback WE choose the format - ask for exactly what the
         * wire wants (48 kHz stereo int16), so there is nothing to convert. */
        memset(&pcm16, 0, sizeof(pcm16));
        pcm16.wFormatTag = WAVE_FORMAT_PCM;
        pcm16.nChannels = 2;
        pcm16.nSamplesPerSec = 48000;
        pcm16.wBitsPerSample = 16;
        pcm16.nBlockAlign = (WORD)(pcm16.nChannels * pcm16.wBitsPerSample / 8);
        pcm16.nAvgBytesPerSec = pcm16.nSamplesPerSec * pcm16.nBlockAlign;
        fmt = &pcm16;
        *out_nch = 2;
        *out_is_float = 0;
        *out_sri = sr_index(48000);
        /* Process loopback requires event-driven mode per the WASAPI contract. */
        flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
    } else {
        ComPtr<IMMDeviceEnumerator> devenum;
        if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL,
                                    IID_PPV_ARGS(&devenum)))) {
            _snprintf_s(why, why_len, _TRUNCATE, "cannot open the audio device list");
            return false;
        }
        ComPtr<IMMDevice> dev;
        HRESULT hr;
        if (source == SRC_DEFAULT_LOOPBACK || device_id.empty()) {
            hr = devenum->GetDefaultAudioEndpoint(
                source == SRC_INPUT_CAPTURE ? eCapture : eRender, eConsole, &dev);
        } else {
            hr = devenum->GetDevice(device_id.c_str(), &dev);
        }
        if (FAILED(hr) || !dev) {
            _snprintf_s(why, why_len, _TRUNCATE, "selected device not available");
            return false;
        }
        if (FAILED(dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, NULL,
                                 (void **)ac.GetAddressOf()))) {
            _snprintf_s(why, why_len, _TRUNCATE, "cannot open the device");
            return false;
        }
        if (FAILED(ac->GetMixFormat(&mix)) || !mix) {
            _snprintf_s(why, why_len, _TRUNCATE, "cannot read the device format");
            return false;
        }

        int nch = mix->nChannels;
        int is_float = 1;
        if (mix->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) {
            is_float = 1;
        } else if (mix->wFormatTag == WAVE_FORMAT_PCM) {
            is_float = 0;
        } else if (mix->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
            WAVEFORMATEXTENSIBLE *ext = (WAVEFORMATEXTENSIBLE *)mix;
            if (IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT))
                is_float = 1;
            else if (IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_PCM))
                is_float = 0;
            else {
                CoTaskMemFree(mix);
                _snprintf_s(why, why_len, _TRUNCATE, "unsupported device format");
                return false;
            }
        } else {
            CoTaskMemFree(mix);
            _snprintf_s(why, why_len, _TRUNCATE, "unsupported device format");
            return false;
        }
        if (!is_float && mix->wBitsPerSample != 16) {
            CoTaskMemFree(mix);
            _snprintf_s(why, why_len, _TRUNCATE, "unsupported bit depth (%d)",
                        (int)mix->wBitsPerSample);
            return false;
        }
        int sri = sr_index((int)mix->nSamplesPerSec);
        if (sri < 0) {
            CoTaskMemFree(mix);
            _snprintf_s(why, why_len, _TRUNCATE, "sample rate %lu not supported by VBAN",
                        (unsigned long)mix->nSamplesPerSec);
            return false;
        }
        *out_nch = nch;
        *out_is_float = is_float;
        *out_sri = sri;
        fmt = mix;
        /* A recording endpoint is captured directly - no loopback flag. */
        if (source == SRC_INPUT_CAPTURE)
            flags = 0;
    }

    HANDLE evt = NULL;
    HRESULT hr = ac->Initialize(AUDCLNT_SHAREMODE_SHARED, flags,
                                2000000 /* 200ms buffer, 100ns units */, 0, fmt, NULL);
    if (SUCCEEDED(hr) && (flags & AUDCLNT_STREAMFLAGS_EVENTCALLBACK)) {
        evt = CreateEventW(NULL, FALSE, FALSE, NULL);
        hr = ac->SetEventHandle(evt);
    }
    if (mix)
        CoTaskMemFree(mix);
    if (FAILED(hr)) {
        if (evt)
            CloseHandle(evt);
        _snprintf_s(why, why_len, _TRUNCATE, "cannot start capture (0x%08lx)", (unsigned long)hr);
        return false;
    }
    if (FAILED(ac->GetService(__uuidof(IAudioCaptureClient), (void **)cap.GetAddressOf())) ||
        FAILED(ac->Start())) {
        if (evt)
            CloseHandle(evt);
        _snprintf_s(why, why_len, _TRUNCATE, "capture service unavailable");
        return false;
    }
    if (evt)
        CloseHandle(evt); /* we poll; the handle only had to exist for Initialize */
    return true;
}

/*
 * Capture thread: open the selected source, pull audio, convert to int16 and
 * stream VBAN. Any failure (device unplugged, app closed, source changed in the
 * dialog) falls through to the cleanup below, backs off and reopens - so the
 * app recovers on its own and never needs restarting.
 */
static DWORD WINAPI capture_thread(LPVOID arg)
{
    (void)arg;
    CoInitializeEx(NULL, COINIT_MULTITHREADED);

    while (InterlockedCompareExchange(&g_running, 1, 1)) {
        ComPtr<IAudioClient> ac;
        ComPtr<IAudioCaptureClient> cap;
        int nch = 2, is_float = 1, sri = -1;
        char why[160] = "";

        InterlockedExchange(&g_reopen, 0);
        if (open_capture(ac, cap, &nch, &is_float, &sri, why, sizeof(why))) {
            InterlockedExchange(&g_capturing, 1);
            set_status("capturing");

            while (InterlockedCompareExchange(&g_running, 1, 1) &&
                   !InterlockedCompareExchange(&g_reopen, 0, 0)) {
                Sleep(5);
                UINT32 packet = 0;
                if (FAILED(cap->GetNextPacketSize(&packet)))
                    break;
                while (packet > 0) {
                    BYTE *data = NULL;
                    UINT32 frames = 0;
                    DWORD flags = 0;
                    if (FAILED(cap->GetBuffer(&data, &frames, &flags, NULL, NULL)))
                        break;
                    if (InterlockedCompareExchange(&g_ready, 1, 1) &&
                        !(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                        process_buffer(data, frames, nch, is_float, sri);
                    cap->ReleaseBuffer(frames);
                    if (FAILED(cap->GetNextPacketSize(&packet)))
                        break;
                }
            }
        } else {
            set_status(why);
        }

        InterlockedExchange(&g_capturing, 0);
        if (ac)
            ac->Stop();
        cap.Reset();
        ac.Reset();
        /* Reopen at once when the user changed the source; otherwise back off. */
        if (InterlockedCompareExchange(&g_running, 1, 1) &&
            !InterlockedCompareExchange(&g_reopen, 0, 0))
            Sleep(1000);
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
        _snprintf_s(detail, detail_len, _TRUNCATE, "socket() failed");
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
            _snprintf_s(detail, detail_len, _TRUNCATE, "cannot resolve host");
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
        _snprintf_s(detail, detail_len, _TRUNCATE, "daemon replied (%s:%d)", host, port);
        return 1;
    }
    _snprintf_s(detail, detail_len, _TRUNCATE, "no reply from %s:%d (daemon running?)", host, port);
    return 0;
}

/* ---- config dialog ---- */
static std::vector<DeviceEntry> g_dlg_devices;   /* what the target combo lists */
static std::vector<ProcessEntry> g_dlg_procs;

/* Refill the target dropdown for the selected source and preselect the saved one. */
static void fill_targets(HWND dlg, LONG source)
{
    HWND combo = GetDlgItem(dlg, IDC_TARGET);
    SendMessageA(combo, CB_RESETCONTENT, 0, 0);
    g_dlg_devices.clear();
    g_dlg_procs.clear();

    EnterCriticalSection(&g_target_lock);
    std::wstring want_dev = g_device_id, want_proc = g_process_name;
    LeaveCriticalSection(&g_target_lock);

    if (source == SRC_DEFAULT_LOOPBACK) {
        EnableWindow(combo, FALSE);
        SendMessageA(combo, CB_ADDSTRING, 0, (LPARAM) "(the default playback device)");
        SendMessageA(combo, CB_SETCURSEL, 0, 0);
        return;
    }
    EnableWindow(combo, TRUE);

    if (source == SRC_PROCESS_LOOPBACK) {
        g_dlg_procs = enum_audio_processes();
        int sel = -1;
        for (size_t i = 0; i < g_dlg_procs.size(); i++) {
            SendMessageA(combo, CB_ADDSTRING, 0, (LPARAM)g_dlg_procs[i].label.c_str());
            if (!want_proc.empty() && _wcsicmp(g_dlg_procs[i].exe.c_str(), want_proc.c_str()) == 0)
                sel = (int)i;
        }
        SendMessageA(combo, CB_SETCURSEL, sel >= 0 ? sel : 0, 0);
        return;
    }

    g_dlg_devices = enum_devices(source == SRC_INPUT_CAPTURE ? eCapture : eRender);
    int sel = -1;
    for (size_t i = 0; i < g_dlg_devices.size(); i++) {
        SendMessageA(combo, CB_ADDSTRING, 0, (LPARAM)g_dlg_devices[i].label.c_str());
        if (!want_dev.empty() && g_dlg_devices[i].id == want_dev)
            sel = (int)i;
    }
    SendMessageA(combo, CB_SETCURSEL, sel >= 0 ? sel : 0, 0);
}

static INT_PTR CALLBACK config_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    (void)lp;
    switch (msg) {
    case WM_INITDIALOG: {
        char portbuf[16];
        SetDlgItemTextA(dlg, IDC_HOST, g_host);
        _snprintf_s(portbuf, sizeof(portbuf), _TRUNCATE, "%d", g_port);
        SetDlgItemTextA(dlg, IDC_PORT, portbuf);
        SetDlgItemTextA(dlg, IDC_STREAM, g_stream);

        HWND src = GetDlgItem(dlg, IDC_SOURCE);
        SendMessageA(src, CB_ADDSTRING, 0, (LPARAM) "Default output (everything you hear)");
        SendMessageA(src, CB_ADDSTRING, 0, (LPARAM) "Specific output device");
        SendMessageA(src, CB_ADDSTRING, 0, (LPARAM) "Input device (Stereo Mix / line-in / mic)");
        SendMessageA(src, CB_ADDSTRING, 0,
                     (LPARAM)(process_loopback_supported()
                                  ? "One application only"
                                  : "One application only (needs Windows 10 2004+)"));
        LONG source = InterlockedCompareExchange(&g_source, 0, 0);
        SendMessageA(src, CB_SETCURSEL, source, 0);
        fill_targets(dlg, source);

        EnterCriticalSection(&g_target_lock);
        SetDlgItemTextA(dlg, IDC_STATUS, g_status);
        LeaveCriticalSection(&g_target_lock);
        SetTimer(dlg, 1, 1000, NULL);
        return TRUE;
    }
    case WM_TIMER: {
        LONG bytes = InterlockedExchange(&g_bytes_acc, 0);
        LONG pkts = InterlockedExchange(&g_pkts_acc, 0);
        char buf[96];
        _snprintf_s(buf, sizeof(buf), _TRUNCATE, "%.1f KB/s  (%ld pkt/s)", bytes / 1024.0,
                    (long)pkts);
        SetDlgItemTextA(dlg, IDC_RATE, buf);
        /* Mirror the capture thread's own status (device gone, app not running...) */
        EnterCriticalSection(&g_target_lock);
        SetDlgItemTextA(dlg, IDC_STATUS, g_status);
        LeaveCriticalSection(&g_target_lock);
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_SOURCE:
            if (HIWORD(wp) == CBN_SELCHANGE) {
                LONG sel = (LONG)SendMessageA(GetDlgItem(dlg, IDC_SOURCE), CB_GETCURSEL, 0, 0);
                if (sel >= 0)
                    fill_targets(dlg, sel);
            }
            return TRUE;
        case IDC_REFRESH: {
            LONG sel = (LONG)SendMessageA(GetDlgItem(dlg, IDC_SOURCE), CB_GETCURSEL, 0, 0);
            if (sel >= 0)
                fill_targets(dlg, sel);
            return TRUE;
        }
        case IDC_TEST: {
            char host[128], portbuf[16], detail[160], line[200];
            int port;
            GetDlgItemTextA(dlg, IDC_HOST, host, sizeof(host));
            GetDlgItemTextA(dlg, IDC_PORT, portbuf, sizeof(portbuf));
            port = atoi(portbuf);
            SetDlgItemTextA(dlg, IDC_STATUS, "testing...");
            UpdateWindow(dlg);
            int ok = test_connection(host, port, detail, sizeof(detail));
            _snprintf_s(line, sizeof(line), _TRUNCATE, "%s %s", ok ? "OK -" : "FAIL -", detail);
            SetDlgItemTextA(dlg, IDC_STATUS, line);
            set_status(line);
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

            LONG old_source = InterlockedCompareExchange(&g_source, 0, 0);
            LONG source = (LONG)SendMessageA(GetDlgItem(dlg, IDC_SOURCE), CB_GETCURSEL, 0, 0);
            if (source < 0)
                source = SRC_DEFAULT_LOOPBACK;
            int tsel = (int)SendMessageA(GetDlgItem(dlg, IDC_TARGET), CB_GETCURSEL, 0, 0);

            EnterCriticalSection(&g_target_lock);
            std::wstring old_dev = g_device_id, old_proc = g_process_name;
            if (source == SRC_PROCESS_LOOPBACK) {
                if (tsel >= 0 && tsel < (int)g_dlg_procs.size())
                    g_process_name = g_dlg_procs[tsel].exe;
            } else if (source == SRC_DEVICE_LOOPBACK || source == SRC_INPUT_CAPTURE) {
                if (tsel >= 0 && tsel < (int)g_dlg_devices.size())
                    g_device_id = g_dlg_devices[tsel].id;
            }
            bool target_changed = (g_device_id != old_dev) || (g_process_name != old_proc);
            LeaveCriticalSection(&g_target_lock);

            InterlockedExchange(&g_source, source);
            save_config();
            resolve_dest(); /* apply the new destination live, no restart */
            if (source != old_source || target_changed) {
                set_status("switching source...");
                InterlockedExchange(&g_reopen, 1); /* capture thread reopens at once */
            }
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
            _snprintf_s(line, sizeof(line), _TRUNCATE, "%s %s",
                        ok ? "Connected -" : "No daemon -", detail);
            g_nid.uFlags = NIF_INFO;
            strcpy_s(g_nid.szInfoTitle, sizeof(g_nid.szInfoTitle), "TuneThatHue");
            strncpy_s(g_nid.szInfo, sizeof(g_nid.szInfo), line, _TRUNCATE);
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
    InitializeCriticalSection(&g_target_lock);

    /* Single instance: look for our own window rather than a named mutex (a
     * mutex reported false positives from left-over kernel objects). */
    if (FindWindowA("TuneThatHueCaptureWnd", NULL))
        return 0;

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
        return 1;
    load_config();
    if (open_socket() != 0)
        return 1;
    InterlockedExchange(&g_ready, 1);

    /* COM for the dialog's device/process enumeration (the capture thread has
     * its own apartment). */
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);

    WNDCLASSA wc;
    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = hinst;
    wc.lpszClassName = "TuneThatHueCaptureWnd";
    RegisterClassA(&wc);
    /* Hidden top-level tool window (never shown, kept out of the taskbar and
     * Alt-Tab), top-level so a second instance can find it by class name. */
    g_hwnd = CreateWindowExA(WS_EX_TOOLWINDOW, "TuneThatHueCaptureWnd", "TuneThatHue",
                             0, 0, 0, 0, 0, NULL, NULL, hinst, NULL);

    memset(&g_nid, 0, sizeof(g_nid));
    g_nid.cbSize = sizeof(g_nid);
    g_nid.hWnd = g_hwnd;
    g_nid.uID = TRAY_UID;
    g_nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAY;
    HICON ico = LoadIconA(hinst, MAKEINTRESOURCEA(IDI_APPICON));
    g_nid.hIcon = ico ? ico : LoadIconA(NULL, (LPCSTR)IDI_APPLICATION);
    strcpy_s(g_nid.szTip, sizeof(g_nid.szTip), "TuneThatHue SoundRecorder");
    Shell_NotifyIconA(NIM_ADD, &g_nid);

    g_thread = CreateThread(NULL, 0, capture_thread, NULL, 0, NULL);

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
    CoUninitialize();
    DeleteCriticalSection(&g_target_lock);
    return (int)msg.wParam;
}
