"""Нарезка N-страничного PDF на N одностраничных файлов (pikepdf).

Маска имени = постоянный текст + переменные:
  {НАЧАЛО:ШАГ}  — нумерация: значение = НАЧАЛО + индекс * ШАГ (шаг по умолчанию +1);
  {DATE +ФОРМАТ} — дата/время запуска в strftime; одна отметка на весь проход.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pikepdf

DEFAULT_MASK = "page_{1:+1}"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
DATE_TOKEN = re.compile(r"\{DATE\s+\+([^}]+)\}")
COUNTER_TOKEN = re.compile(r"\{(-?\d+)(?::([+-]?\d+))?\}")


def sanitize_filename(name: str) -> str:
    """Убрать недопустимые для Windows символы и добавить .pdf при необходимости."""
    name = INVALID_FILENAME_CHARS.sub("_", name.strip())
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def render_filename(mask: str, index: int, run_at: datetime) -> str:
    """Подставить токены маски для файла с порядковым индексом 0..N-1."""

    def replace_date(match: re.Match[str]) -> str:
        return run_at.strftime(match.group(1))

    name = DATE_TOKEN.sub(replace_date, mask)

    def replace_counter(match: re.Match[str]) -> str:
        start = int(match.group(1))
        step = int(match.group(2)) if match.group(2) is not None else 1
        return str(start + index * step)

    name = COUNTER_TOKEN.sub(replace_counter, name)
    return sanitize_filename(name)


def proposed_names(mask: str, count: int, run_at: datetime) -> list[str]:
    return [render_filename(mask, index, run_at) for index in range(count)]


def page_count(src: str | Path) -> int:
    with pikepdf.open(src) as pdf:
        return len(pdf.pages)


def _write_page(pdf: pikepdf.Pdf, index: int, dest: Path) -> Path:
    dest = dest.with_name(sanitize_filename(dest.name))
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = pikepdf.Pdf.new()
    try:
        out.pages.append(pdf.pages[index])
        out.save(dest)
    finally:
        out.close()
    return dest


def export_pages(src: str | Path, items: list[tuple[int, Path]]) -> list[Path]:
    """Сохранить указанные страницы в указанные файлы. Индекс страницы — с нуля."""
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"Файл не найден: {src_path}")

    written: list[Path] = []
    with pikepdf.open(src_path) as pdf:
        total = len(pdf.pages)
        for index, dest in items:
            if index < 0 or index >= total:
                raise IndexError(f"Страницы {index + 1} нет в PDF ({total} стр.)")
            written.append(_write_page(pdf, index, Path(dest)))
    return written


def split_pdf(
    src: str | Path,
    out_dir: str | Path | None = None,
    mask: str = DEFAULT_MASK,
    run_at: datetime | None = None,
) -> list[Path]:
    """Разрезать PDF на одностраничные файлы. Возвращает пути записанных файлов."""
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"Файл не найден: {src_path}")

    dest_dir = Path(out_dir) if out_dir is not None else src_path.resolve().parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_at if run_at is not None else datetime.now()
    written: list[Path] = []

    with pikepdf.open(src_path) as pdf:
        for index in range(len(pdf.pages)):
            dest = dest_dir / render_filename(mask, index, stamp)
            written.append(_write_page(pdf, index, dest))

    return written


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Нарезать PDF на одностраничные файлы.",
    )
    parser.add_argument("input", type=Path, help="Исходный PDF")
    parser.add_argument(
        "-d",
        "--out-dir",
        type=Path,
        default=None,
        help="Каталог вывода (по умолчанию — рядом с исходным PDF)",
    )
    parser.add_argument(
        "-m",
        "--mask",
        default=DEFAULT_MASK,
        help=f"Маска имён файлов (по умолчанию: {DEFAULT_MASK})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        paths = split_pdf(args.input, out_dir=args.out_dir, mask=args.mask)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if not paths:
        print("В PDF нет страниц.")
        return 0

    print(f"Готово: {len(paths)} файл(ов)")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
