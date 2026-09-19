#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_usb.py

Windows USB-Printer-class (usbprint.sys) device discovery, raw
CreateFile/WriteFile delivery, and a small Tk picker window - for
sending a job to the SkyCut cutter (USB ID 0483:5750) over USB.

Used by skycut_output.py's send_hpgl_usb(), called from
skycut_command_d24_1.py's "Send to Machine USB" action.

Windows only. Uses ctypes (stdlib) - no third-party dependencies.
The picker window uses tkinter, which ships with Inkscape's bundled
Python on Windows.

Note: this device is USB-Printer class, not a virtual COM port -
there is no baud rate to set (that's a serial concept and doesn't
apply here).
"""

import ctypes
from ctypes import wintypes

VENDOR_ID = 0x0483
PRODUCT_ID = 0x5750

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_USBPRINT = GUID(
    0x28D78FAD, 0x5A12, 0x11D1,
    (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2),
)


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


def _get_win32():
    setupapi = ctypes.windll.setupapi
    kernel32 = ctypes.windll.kernel32

    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
    ]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID),
        wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(SP_DEVINFO_DATA)
    ]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]
    return setupapi, kernel32


def _detail_data_cbsize():
    return 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


def find_usbprint_paths(vid=VENDOR_ID, pid=PRODUCT_ID):
    """Return device interface paths for every USB-Printer-class device matching vid:pid."""
    setupapi, kernel32 = _get_win32()
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    h_devinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_USBPRINT), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if h_devinfo == INVALID_HANDLE_VALUE or not h_devinfo:
        return []

    results = []
    index = 0
    try:
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                h_devinfo, None, ctypes.byref(GUID_DEVINTERFACE_USBPRINT),
                index, ctypes.byref(ifdata)
            )
            if not ok:
                break

            required = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                h_devinfo, ctypes.byref(ifdata), None, 0,
                ctypes.byref(required), None
            )
            buf = ctypes.create_string_buffer(required.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = _detail_data_cbsize()

            devinfo_data = SP_DEVINFO_DATA()
            devinfo_data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)

            ok2 = setupapi.SetupDiGetDeviceInterfaceDetailW(
                h_devinfo, ctypes.byref(ifdata), buf, required.value,
                None, ctypes.byref(devinfo_data)
            )
            if ok2:
                path = ctypes.wstring_at(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD))
                vid_str = f"vid_{vid:04x}"
                pid_str = f"pid_{pid:04x}"
                if vid_str in path.lower() and pid_str in path.lower():
                    results.append(path)

            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h_devinfo)

    return results


def send_job(device_path, payload):
    """Open device_path and WriteFile payload (bytes) to it. Returns bytes written."""
    _, kernel32 = _get_win32()
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(
        device_path, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if handle == INVALID_HANDLE_VALUE or not handle:
        err = ctypes.get_last_error()
        raise OSError(f"CreateFile failed (WinError {err})")

    try:
        written = wintypes.DWORD(0)
        ok = kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"WriteFile failed (WinError {err})")
        return written.value
    finally:
        kernel32.CloseHandle(handle)


def friendly_label(path):
    # Device path looks like:
    #   \\?\usb#vid_0483&pid_5750#skycut_5503#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}
    # Pull out the serial-ish middle segment for a readable label.
    parts = path.strip("\\").lstrip("?\\").split("#")
    ident = parts[2] if len(parts) > 2 else path
    return f"{ident}"


def pick_and_send(paths, payload, title="SkyCut - Send to Machine USB"):
    """
    Show a small Tk window listing `paths` (device interface paths
    from find_usbprint_paths()), let the user pick one and click
    Send, and write `payload` (bytes) to it via send_job(). Blocks
    until the window closes.

    Always shown when there's at least one candidate device - even
    just one - so there's a visible confirmation step before
    anything is written to a physical device, matching the original
    "Send Test Job (USB)" picker's behavior.

    Returns (success, message) - message is a short, user-facing
    status/error string. success is False if the window was closed
    or Cancel was clicked without sending.

    Raises ImportError if tkinter isn't available - callers should
    catch this and fall back to a non-interactive path.
    """
    import tkinter as tk

    result = {"success": False, "message": "Cancelled - no job sent."}

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.lift()
    root.after(0, lambda: root.attributes("-topmost", False))

    tk.Label(
        root,
        text="SkyCut(s) found (USB-Printer class, VID 0483:PID 5750) - pick one:",
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 4))

    listbox = tk.Listbox(root, width=70, height=min(6, max(2, len(paths))), exportselection=False)
    for p in paths:
        listbox.insert(tk.END, friendly_label(p))
    listbox.selection_set(0)
    listbox.pack(padx=12, pady=(0, 4))

    tk.Label(root, text="Connection: USB (Printer class) -- no baud rate; not a serial port.",
             fg="gray30", anchor="w").pack(fill="x", padx=12, pady=(0, 8))

    status = tk.Label(root, text="", anchor="w", justify="left", wraplength=420)
    status.pack(fill="x", padx=12, pady=(0, 8))

    def on_send():
        sel = listbox.curselection()
        if not sel:
            status.config(text="Select a device first.", fg="red")
            return
        device_path = paths[sel[0]]
        status.config(text="Sending...", fg="blue")
        root.update_idletasks()
        try:
            written = send_job(device_path, payload)
            result["success"] = True
            result["message"] = f"Sent {written} bytes to {friendly_label(device_path)} via USB"
            status.config(text=result["message"], fg="green")
            root.after(800, root.destroy)
        except OSError as e:
            result["success"] = False
            result["message"] = str(e)
            status.config(text=f"Failed: {e}", fg="red")

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=(0, 12))
    tk.Button(btn_frame, text="Send", command=on_send, width=14).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Cancel", command=root.destroy, width=10).pack(side="left", padx=6)

    root.mainloop()
    return result["success"], result["message"]
