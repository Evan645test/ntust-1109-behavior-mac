#!/usr/bin/env python3
"""臺科 1109 行為分析素材包的 Mac 選單。"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import gseq_mac
import mps_mac

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parent
OUTPUT = ROOT / "輸出"


def pause(message: str = "按 Enter 繼續...") -> None:
    try:
        input(message)
    except EOFError:
        return


def open_path(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


def parse_dropped_paths(raw: str) -> list[Path]:
    text = raw.strip()
    if not text:
        return []
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    return [Path(part).expanduser() for part in parts]


def convert_paths(paths: list[Path], output: Path) -> int:
    try:
        inputs = mps_mac.collect_inputs(paths)
        converted = mps_mac.convert(inputs, output, append=False)
    except (FileNotFoundError, OSError) as exc:
        print(str(exc))
        return 1
    print(f"已寫入 {output}")
    for path, codes in converted:
        preview = " ".join(codes) if codes else "（沒有 A–Z 開頭的行）"
        print(f"  {path.name}: {len(codes)} 個代碼  {preview}")
    return 0


def analyze_paths(paths: list[Path], output: Path, codes: list[str] | None = None) -> int:
    try:
        sessions, declared = gseq_mac.load_inputs(paths)
        tables = gseq_mac.build_tables(sessions, declared, requested=codes, drop_repeats=False)
        gseq_mac.write_outputs(output, tables, sessions)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    print(f"已寫入 {output}")
    print(f"已寫入 {output.with_suffix('.txt')}")
    return 0


def demo() -> int:
    save_groups = [
        (PACKAGE / "01.資料轉換" / "save.txt", OUTPUT / "01-範例save轉換.txt"),
        (PACKAGE / "02.數據資料" / "01.資料轉換(數據包)", OUTPUT / "02-數據包轉換.txt"),
        (PACKAGE / "03.小活動", OUTPUT / "03-小活動轉換.txt"),
    ]
    for source, output in save_groups:
        print(f"\n轉換 {source.name}")
        status = convert_paths([source], output)
        if status != 0:
            return status
    report = OUTPUT / "行為轉換報告.html"
    students = PACKAGE / "02.數據資料" / "02.GSEQ(數據包)"
    print(f"\n分析 {students.name}")
    status = analyze_paths([students], report)
    if status != 0:
        return status
    open_path(report)
    return 0


def ask_paths(prompt: str) -> list[Path]:
    print(prompt)
    print("可以把檔案或資料夾從 Finder 拖進這個視窗。多個檔案用空白分開。")
    raw = input("> ")
    return parse_dropped_paths(raw)


def menu() -> int:
    actions = {
        "1": "轉換 save 紀錄（取代 MPS.exe）",
        "2": "計算行為轉換（取代 GSEQ）",
        "3": "跑素材包範例並打開報告",
        "4": "離開",
    }
    while True:
        print()
        print("臺科 1109 行為分析 · Mac 版")
        for key, label in actions.items():
            print(f"  {key}. {label}")
        choice = input("請輸入數字：").strip()
        if choice == "4":
            return 0
        if choice == "3":
            status = demo()
            pause()
            if status != 0:
                return status
            continue
        if choice == "1":
            paths = ask_paths("要轉換哪些 save.txt？")
            if not paths:
                print("沒有收到檔案。")
                continue
            output = OUTPUT / "result.txt"
            status = convert_paths(paths, output)
            if status == 0:
                open_path(output)
            pause()
            continue
        if choice == "2":
            paths = ask_paths("要分析哪些序列 txt？可以是 result.txt，或貼好的 SDS 文字檔。")
            if not paths:
                print("沒有收到檔案。")
                continue
            print("操作教學(三) 的編碼是 A 到 F。")
            print("  1. 只分析 A–F")
            print("  直接按 Enter：檔案裡出現的代碼都分析")
            code_choice = input("代碼範圍：").strip()
            codes = list("ABCDEF") if code_choice == "1" else None
            output = OUTPUT / "行為轉換報告.html"
            status = analyze_paths(paths, output, codes)
            if status == 0:
                open_path(output)
            pause()
            continue
        print("請輸入 1 到 4。")


def main() -> int:
    try:
        return menu()
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
