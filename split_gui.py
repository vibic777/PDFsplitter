"""GUI-нарезка PDF: маска → имена всех страниц → правка текущего имени по превью."""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pypdfium2 as pdfium
from PIL import Image, ImageTk

from split_pdf import DEFAULT_MASK, export_pages, page_count, proposed_names, sanitize_filename

PREVIEW_SCALE = 2.0
MAX_PX_PER_PT = 8.0
ZOOM_STEP = 1.25
MIN_ZOOM_VS_FIT = 0.25
MASK_EXAMPLE = "Акт о внедрении {216:+1} 27.06.2026"


def _enable_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def render_page_image(doc: pdfium.PdfDocument, index: int, scale: float = PREVIEW_SCALE) -> Image.Image:
    page = doc[index]
    try:
        return page.render(scale=scale).to_pil()
    finally:
        page.close()


def page_size_pts(doc: pdfium.PdfDocument, index: int) -> tuple[float, float]:
    page = doc[index]
    try:
        width, height = page.get_size()
        return float(width), float(height)
    finally:
        page.close()


class SplitterApp(tk.Tk):
    def __init__(self, initial_pdf: Path | None = None) -> None:
        super().__init__()
        self.title("PDFsplitter")
        self.minsize(1040, 720)
        self.geometry("1200x820")

        self.src: Path | None = None
        self.run_at: datetime | None = None
        self.page_total = 0
        self.templates: list[str] = []
        self.names: list[str] = []
        self.saved: list[bool] = []
        self.current = 0
        self._pdfium: pdfium.PdfDocument | None = None
        self._pil: Image.Image | None = None
        self._pil_scale = 0.0
        self._page_pts: tuple[float, float] | None = None
        self._abs_scale: float | None = None
        self._disp_size = (1, 1)
        self._photo: ImageTk.PhotoImage | None = None
        self._busy = False
        self._last_canvas = (0, 0)

        self.pdf_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.mask_var = tk.StringVar(value=DEFAULT_MASK)
        self.name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Укажите PDF и маску, затем сформируйте имена")
        self.page_var = tk.StringVar(value="—")
        self.zoom_var = tk.StringVar(value="100%")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-s>", lambda _e: self._save_and_next())
        self.canvas.bind("<Left>", lambda _e: self._step(-1))
        self.canvas.bind("<Right>", lambda _e: self._step(1))
        self.canvas.bind("<plus>", lambda _e: self._zoom_by(ZOOM_STEP))
        self.canvas.bind("<minus>", lambda _e: self._zoom_by(1 / ZOOM_STEP))
        self.canvas.bind("<KP_Add>", lambda _e: self._zoom_by(ZOOM_STEP))
        self.canvas.bind("<KP_Subtract>", lambda _e: self._zoom_by(1 / ZOOM_STEP))
        self.canvas.bind("<Control-0>", lambda _e: self._zoom_fit())

        if initial_pdf is not None:
            self.pdf_var.set(str(initial_pdf))
            self.after(50, self._open_pdf)

    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 4))

        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="PDF").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(top, textvariable=self.pdf_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Обзор…", command=self._browse_pdf).grid(row=0, column=2, padx=(8, 0), pady=3)

        ttk.Label(top, text="Папка").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(top, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Обзор…", command=self._browse_out).grid(row=1, column=2, padx=(8, 0), pady=3)

        ttk.Label(top, text="Маска").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(top, textvariable=self.mask_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Сформировать имена", command=self._form_names).grid(
            row=2, column=2, padx=(8, 0), pady=3
        )

        hint = ttk.Label(
            top,
            text=(
                f"Маска задаёт имена сразу всем страницам. Пример: {MASK_EXAMPLE} "
                "→ 216, 217, 218… и одна дата. Дальше смотрите превью и у нужной "
                "страницы поправьте имя (дату и т.д.)."
            ),
            foreground="#555",
            wraplength=980,
            justify=tk.LEFT,
        )
        hint.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 8))

        mid = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        mid.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(mid, padding=(0, 0, 8, 0))
        right = ttk.Frame(mid)
        mid.add(left, weight=1)
        mid.add(right, weight=4)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=0)

        ttk.Label(left, text="Имена по шаблону").grid(row=0, column=0, sticky="w")
        self.listbox = tk.Listbox(
            left,
            activestyle="dotbox",
            font=("Consolas", 10),
            exportselection=False,
            selectmode=tk.SINGLE,
            width=42,
        )
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        preview = ttk.Frame(right)
        preview.grid(row=0, column=0, sticky="nsew")
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(preview, background="#2b2b2b", highlightthickness=0)
        self.scroll_y = ttk.Scrollbar(preview, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scroll_x = ttk.Scrollbar(preview, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.scroll_x.set, yscrollcommand=self.scroll_y.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_y.grid(row=0, column=1, sticky="ns")
        self.scroll_x.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)

        zoombar = ttk.Frame(right)
        zoombar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(zoombar, text="−", width=3, command=lambda: self._zoom_by(1 / ZOOM_STEP)).pack(side=tk.LEFT)
        ttk.Label(zoombar, textvariable=self.zoom_var, width=8, anchor="center").pack(side=tk.LEFT, padx=4)
        ttk.Button(zoombar, text="+", width=3, command=lambda: self._zoom_by(ZOOM_STEP)).pack(side=tk.LEFT)
        ttk.Button(zoombar, text="По странице", command=self._zoom_fit).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(
            zoombar,
            text="Ctrl+колёсико — масштаб · колёсико — вверх/вниз · Shift+колёсико — влево/вправо · тянуть мышью — сдвиг",
            foreground="#555",
        ).pack(side=tk.LEFT, padx=(12, 0))

        bottom = ttk.LabelFrame(root, text="Текущая страница — имя из шаблона, можно исправить", padding=8)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)

        name_entry = ttk.Entry(bottom, textvariable=self.name_var, font=("Segoe UI", 12))
        name_entry.grid(row=0, column=0, sticky="ew")
        name_entry.bind("<KeyRelease>", lambda _e: self._store_name())
        name_entry.bind("<FocusOut>", lambda _e: self._commit_name())
        name_entry.bind("<Return>", lambda _e: self._save_and_next())

        nav = ttk.Frame(bottom)
        nav.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(nav, text="◀ Назад", command=lambda: self._step(-1)).pack(side=tk.LEFT)
        ttk.Label(nav, textvariable=self.page_var, width=12, anchor="center").pack(side=tk.LEFT, padx=8)
        ttk.Button(nav, text="Вперёд ▶", command=lambda: self._step(1)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Сохранить и далее", command=self._save_and_next).pack(side=tk.LEFT, padx=(20, 0))
        ttk.Button(nav, text="Сохранить все", command=self._save_all).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(root, textvariable=self.status_var, foreground="#333").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )

    def _browse_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Исходный PDF",
            filetypes=[("PDF", "*.pdf"), ("Все файлы", "*.*")],
        )
        if path:
            self.pdf_var.set(path)
            self._open_pdf()

    def _browse_out(self) -> None:
        path = filedialog.askdirectory(title="Папка вывода")
        if path:
            self.out_var.set(path)

    def _on_close(self) -> None:
        self._close_doc()
        self.destroy()

    def _close_doc(self) -> None:
        if self._pdfium is not None:
            try:
                self._pdfium.close()
            except Exception:
                pass
            self._pdfium = None
        self._pil = None
        self._photo = None
        self._pil_scale = 0.0
        self._page_pts = None

    def _open_pdf(self) -> None:
        raw = self.pdf_var.get().strip().strip('"')
        if not raw:
            messagebox.showwarning("PDF", "Укажите исходный файл.")
            return
        src = Path(raw)
        if not src.is_file():
            messagebox.showerror("PDF", f"Файл не найден:\n{src}")
            return
        try:
            total = page_count(src)
        except Exception as exc:
            messagebox.showerror("PDF", str(exc))
            return
        if total == 0:
            messagebox.showwarning("PDF", "В файле нет страниц.")
            return

        self._close_doc()
        try:
            self._pdfium = pdfium.PdfDocument(str(src))
        except Exception as exc:
            messagebox.showerror("Превью", f"Не удалось открыть превью:\n{exc}")
            return

        self.src = src
        self.page_total = total
        self.current = 0
        self._abs_scale = None
        self._pil = None
        self._pil_scale = 0.0
        self._page_pts = None
        if not self.out_var.get().strip():
            self.out_var.set(str(src.resolve().parent))
        self._form_names(confirm=False)
        self.status_var.set(
            f"{src.name}: {total} стр. Имена сформированы по маске. "
            "Листайте превью и правьте имя текущей страницы при необходимости."
        )

    def _form_names(self, confirm: bool = True) -> None:
        if self.src is None or self.page_total <= 0:
            if confirm:
                self._open_pdf()
            return
        if confirm and self.names and (any(self.saved) or self._has_edits()):
            if not messagebox.askyesno(
                "Сформировать имена",
                "Пересчитать имена всех страниц по маске? Ручные правки будут заменены.",
            ):
                return

        self.run_at = datetime.now()
        generated = proposed_names(self.mask_var.get(), self.page_total, self.run_at)
        self.templates = list(generated)
        self.names = list(generated)
        self.saved = [False] * self.page_total
        if self.current >= self.page_total:
            self.current = 0
        self._refresh_list()
        self._show_page(self.current)
        self.status_var.set(
            f"Сформировано имён: {self.page_total}. "
            f"Первое: {self.names[0]}  ·  последнее: {self.names[-1]}"
        )

    def _has_edits(self) -> bool:
        return self.names != self.templates

    def _out_dir(self) -> Path | None:
        raw = self.out_var.get().strip().strip('"')
        if not raw:
            if self.src is not None:
                return self.src.resolve().parent
            return None
        return Path(raw)

    def _store_name(self) -> None:
        if not self.names:
            return
        self.names[self.current] = self.name_var.get()

    def _commit_name(self) -> None:
        self._store_name()
        if self.names:
            self._refresh_list_item(self.current)

    def _refresh_list(self) -> None:
        self._busy = True
        try:
            self.listbox.delete(0, tk.END)
            for i, name in enumerate(self.names):
                self.listbox.insert(tk.END, self._row_text(i, name))
            if self.names:
                self.listbox.selection_set(self.current)
                self.listbox.see(self.current)
        finally:
            self._busy = False

    def _refresh_list_item(self, index: int) -> None:
        if index >= self.listbox.size():
            return
        self._busy = True
        try:
            self.listbox.delete(index)
            self.listbox.insert(index, self._row_text(index, self.names[index]))
            self.listbox.selection_set(self.current)
        finally:
            self._busy = False

    def _row_text(self, index: int, name: str) -> str:
        if self.saved[index]:
            mark = "✓"
        elif index < len(self.templates) and name != self.templates[index]:
            mark = "*"
        else:
            mark = " "
        stem = name[:-4] if name.lower().endswith(".pdf") else name
        return f"{mark} {index + 1:>3}  {stem}"

    def _on_list_select(self, _event: tk.Event | None = None) -> None:
        if self._busy:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        self._commit_name()
        self._show_page(int(sel[0]))

    def _step(self, delta: int) -> None:
        if not self.names:
            return
        self._commit_name()
        nxt = max(0, min(len(self.names) - 1, self.current + delta))
        self._show_page(nxt)

    def _on_mousewheel(self, event: tk.Event) -> str:
        if event.state & 0x0004:
            factor = ZOOM_STEP if event.delta > 0 else 1 / ZOOM_STEP
            self._zoom_by(factor, pivot=(event.x, event.y))
            return "break"
        steps = -int(event.delta / 120) if event.delta else 0
        if steps == 0:
            steps = -1 if event.delta > 0 else 1
        if event.state & 0x0001:
            self.canvas.xview_scroll(steps, "units")
        else:
            self.canvas.yview_scroll(steps, "units")
        return "break"

    def _on_pan_start(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self.canvas.scan_mark(event.x, event.y)

    def _on_pan_move(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _show_page(self, index: int) -> None:
        if not self.names:
            return
        self.current = index
        self._busy = True
        try:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        finally:
            self._busy = False
        self.name_var.set(self.names[index])
        self.page_var.set(f"{index + 1} / {len(self.names)}")
        self._pil = None
        self._pil_scale = 0.0
        self._page_pts = None
        self._render_preview()

    def _view_size(self) -> tuple[int, int]:
        return max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)

    def _fit_scale(self) -> float:
        if self._page_pts is None:
            return 1.0
        cw, ch = self._view_size()
        pad = 8
        pw, ph = self._page_pts
        return min((cw - pad) / max(pw, 1.0), (ch - pad) / max(ph, 1.0), MAX_PX_PER_PT)

    def _display_scale(self) -> float:
        fit = self._fit_scale()
        if self._abs_scale is None:
            return fit
        low = fit * MIN_ZOOM_VS_FIT
        return max(low, min(self._abs_scale, MAX_PX_PER_PT))

    def _zoom_by(self, factor: float, pivot: tuple[int, int] | None = None) -> None:
        if self._pdfium is None or self._page_pts is None:
            return
        cw, ch = self._view_size()
        old_scale = self._display_scale()
        if pivot is None:
            pivot = (cw // 2, ch // 2)
        old_x = self.canvas.canvasx(pivot[0])
        old_y = self.canvas.canvasy(pivot[1])
        fit = self._fit_scale()
        self._abs_scale = max(fit * MIN_ZOOM_VS_FIT, min(old_scale * factor, MAX_PX_PER_PT))
        self._paint_preview()
        new_scale = self._display_scale()
        ratio = new_scale / old_scale if old_scale else 1.0
        self._scroll_point_to((old_x * ratio, old_y * ratio), pivot)

    def _zoom_fit(self) -> None:
        self._abs_scale = None
        self._paint_preview()

    def _scroll_point_to(self, image_xy: tuple[float, float], widget_xy: tuple[int, int]) -> None:
        dw, dh = self._disp_size
        left = image_xy[0] - widget_xy[0]
        top = image_xy[1] - widget_xy[1]
        self.canvas.xview_moveto(max(left, 0) / max(dw, 1))
        self.canvas.yview_moveto(max(top, 0) / max(dh, 1))

    def _render_preview(self) -> None:
        self.canvas.delete("all")
        if self._pdfium is None:
            self._draw_placeholder("Выберите PDF")
            return
        try:
            self._page_pts = page_size_pts(self._pdfium, self.current)
        except Exception as exc:
            self._page_pts = None
            self._pil = None
            self._draw_placeholder(f"Нет превью:\n{exc}")
            return
        self._paint_preview()

    def _ensure_bitmap(self, needed_scale: float) -> Image.Image | None:
        if self._pdfium is None:
            return None
        if self._pil is not None and self._pil_scale >= needed_scale * 0.92:
            return self._pil
        scale = min(max(needed_scale, PREVIEW_SCALE), MAX_PX_PER_PT)
        try:
            self._pil = render_page_image(self._pdfium, self.current, scale=scale)
            self._pil_scale = scale
        except Exception:
            return None
        return self._pil

    def _on_canvas_resize(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._last_canvas:
            return
        self._last_canvas = size
        if self._page_pts is not None:
            self._paint_preview()

    def _paint_preview(self) -> None:
        if self._page_pts is None:
            return
        scale = self._display_scale()
        pw, ph = self._page_pts
        dw = max(1, int(pw * scale))
        dh = max(1, int(ph * scale))
        bitmap = self._ensure_bitmap(scale)
        if bitmap is None:
            self._draw_placeholder("Нет превью")
            return
        if bitmap.size != (dw, dh):
            shown = bitmap.resize((dw, dh), Image.Resampling.LANCZOS)
        else:
            shown = bitmap
        self._photo = ImageTk.PhotoImage(shown)
        self._disp_size = (dw, dh)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor=tk.NW)
        self.canvas.configure(scrollregion=(0, 0, dw, dh))
        fit = self._fit_scale()
        percent = int(round(scale / fit * 100)) if fit else 100
        self.zoom_var.set("100%" if self._abs_scale is None else f"{percent}%")

    def _draw_placeholder(self, text: str) -> None:
        self.canvas.delete("all")
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.canvas.create_text(
            cw // 2, ch // 2, text=text, fill="#dddddd", font=("Segoe UI", 14), justify=tk.CENTER
        )
        self.canvas.configure(scrollregion=(0, 0, cw, ch))
        self.zoom_var.set("—")

    def _save_current(self) -> bool:
        if not self._ready_to_save():
            return False
        self._commit_name()
        dest = self._dest_for(self.current)
        try:
            export_pages(self.src, [(self.current, dest)])
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc))
            return False
        self.saved[self.current] = True
        self.names[self.current] = dest.name
        self.name_var.set(dest.name)
        self._refresh_list_item(self.current)
        self.status_var.set(f"Сохранено: {dest.name}")
        return True

    def _save_and_next(self) -> None:
        if not self._save_current():
            return
        if self.current < len(self.names) - 1:
            self._step(1)

    def _save_all(self) -> None:
        if not self._ready_to_save():
            return
        self._commit_name()
        items: list[tuple[int, Path]] = []
        seen: dict[str, int] = {}
        for index, name in enumerate(self.names):
            dest = self._dest_for(index)
            key = dest.name.lower()
            if key in seen:
                messagebox.showerror(
                    "Имена",
                    f"Одинаковое имя у страниц {seen[key] + 1} и {index + 1}:\n{dest.name}",
                )
                return
            seen[key] = index
            items.append((index, dest))
        try:
            written = export_pages(self.src, items)
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc))
            return
        self.saved = [True] * len(self.names)
        for index, path in enumerate(written):
            self.names[index] = path.name
        self.name_var.set(self.names[self.current])
        self._refresh_list()
        self.status_var.set(f"Сохранено файлов: {len(written)} → {written[0].parent}")

    def _dest_for(self, index: int) -> Path:
        out = self._out_dir()
        assert out is not None
        return out / sanitize_filename(self.names[index])

    def _ready_to_save(self) -> bool:
        if self.src is None or not self.names:
            messagebox.showwarning("PDF", "Сначала укажите PDF и сформируйте имена.")
            return False
        if self._out_dir() is None:
            messagebox.showwarning("Папка", "Укажите папку вывода.")
            return False
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI: нарезка PDF с превью и правкой имени.")
    parser.add_argument("input", nargs="?", type=Path, help="Исходный PDF")
    args = parser.parse_args(argv)
    _enable_windows_dpi()
    initial = args.input if args.input else None
    app = SplitterApp(initial_pdf=initial)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
