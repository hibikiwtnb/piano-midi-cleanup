"""Open the main GUI and save its window for remote visual review."""

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time
import traceback
import tkinter as tk
from tkinter import ttk

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_PATH = PROJECT_ROOT / 'docs' / 'gui-preview.png'
LOG_PATH = PROJECT_ROOT / 'docs' / 'gui-preview.log'
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from gui_i18n import tr
from postprocess_panel import Panel, PARAMETER_TABS


def render_window(window):
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ]
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(window, ctypes.byref(bounds)):
        raise RuntimeError('Could not read window bounds.')
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    source_dc = user32.GetWindowDC(window)
    memory_dc = gdi32.CreateCompatibleDC(source_dc)
    bitmap = gdi32.CreateCompatibleBitmap(source_dc, width, height)
    previous_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(window, memory_dc, 2):
            raise RuntimeError('Window rendering failed.')
        import struct
        header = ctypes.create_string_buffer(
            struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0),
            44,
        )
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, header, 0):
            raise RuntimeError('Could not read the rendered window.')
        image = Image.frombytes('RGB', (width, height), pixels.raw, 'raw', 'BGRX')
        return image
    finally:
        gdi32.SelectObject(memory_dc, previous_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(window, source_dc)


def capture_window(window, refresh=None, output_path=SCREENSHOT_PATH):
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    bounds = wintypes.RECT()
    user32.GetWindowRect(window, ctypes.byref(bounds))
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    screen_width, screen_height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    image = Image.new('RGB', (width, height))
    try:
        # Paint each portion inside the remote session's small display surface.
        for top in sorted({0, max(0, height - screen_height)}):
            for left in sorted({0, max(0, width - screen_width)}):
                user32.SetWindowPos(window, None, -left, -top, 0, 0, 21)
                if refresh:
                    refresh()
                time.sleep(0.2)
                tile = render_window(window)
                box = (left, top, min(width, left + screen_width), min(height, top + screen_height))
                image.paste(tile.crop(box), (left, top))
    finally:
        user32.SetWindowPos(window, None, bounds.left, bounds.top, 0, 0, 21)
        if refresh:
            refresh()
    image.save(output_path)
    LOG_PATH.write_text(
        f'Saved {output_path}\nSize: {width} x {height}\n'
        f'Session display: {screen_width} x {screen_height}\n',
        encoding='utf-8',
    )


def main(tab=None, all_report_pages=False):
    root = tk.Tk()
    panel = Panel(root)
    if all_report_pages:
        tab = 'report'
    if tab:
        title = tr('Detailed report') if tab == 'report' else tr(dict(PARAMETER_TABS)[tab])
        pending = [root]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == 'TNotebook':
                for page in widget.tabs():
                    if widget.tab(page, 'text') == title:
                        widget.select(page)
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    root.maxsize(max(width, 2000), max(height, 1500))
    root.geometry(f'{width}x{height}+0+0')

    def capture():
        try:
            root.update_idletasks()
            get_ancestor = ctypes.windll.user32.GetAncestor
            get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
            get_ancestor.restype = wintypes.HWND
            window = get_ancestor(root.winfo_id(), 2)
            if not window:
                raise RuntimeError('Could not identify the GUI window.')
            root.lift()
            root.update_idletasks()
            if all_report_pages:
                tree = panel.report_tree
                tree.yview_moveto(0)
                root.update()
                row_height = int(ttk.Style(root).lookup('Treeview', 'rowheight') or 20)
                step = max(1, (tree.winfo_height() - row_height) // row_height - 2)
                paths = []
                while True:
                    output = SCREENSHOT_PATH.with_name(f'gui-report-page-{len(paths) + 1:02d}.png')
                    capture_window(window, root.update, output)
                    paths.append(str(output))
                    first, last = tree.yview()
                    if last >= 1:
                        break
                    tree.yview_scroll(step, 'units')
                    root.update()
                    if tree.yview()[0] <= first:
                        raise RuntimeError('Report scrolling did not advance.')
                LOG_PATH.write_text('Saved report pages:\n' + '\n'.join(paths) + '\n', encoding='utf-8')
            else:
                capture_window(window, root.update)
        except Exception:
            LOG_PATH.write_text(traceback.format_exc(), encoding='utf-8')

    root.after(1500, capture)
    root.mainloop()


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--tab', choices=[name for name, _ in PARAMETER_TABS] + ['report'])
        parser.add_argument('--all-report-pages', action='store_true')
        args = parser.parse_args()
        main(tab=args.tab, all_report_pages=args.all_report_pages)
    except Exception:
        LOG_PATH.write_text(traceback.format_exc(), encoding='utf-8')
