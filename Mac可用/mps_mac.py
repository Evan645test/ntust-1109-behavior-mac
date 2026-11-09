#!/usr/bin/env python3
"""把課堂 save 紀錄轉成 GSEQ 事件序列。規則與素材包內的 MPS.exe v1.0 相同。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STUDENT_RE = re.compile(r"% Student #(\d+)")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1")


def codes_from_save(text: str) -> list[str]:
    """每一行只看第一個字元；A–Z 才留下。與 MPS.exe 的 getline 迴圈一致。"""
    codes: list[str] = []
    for line in text.splitlines():
        if not line:
            continue
        first = line[0]
        if "A" <= first <= "Z":
            codes.append(first)
    return codes


def session_block(student_number: int, codes: list[str]) -> str:
    body = "".join(f" {code}" for code in codes)
    return f"\n\n% Student #{student_number}\n{body};\n"


def next_student_number(existing: str) -> int:
    numbers = [int(match) for match in STUDENT_RE.findall(existing)]
    return max(numbers) + 1 if numbers else 1


def collect_inputs(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted(
                child
                for child in path.iterdir()
                if child.is_file()
                and child.suffix.lower() == ".txt"
                and child.name.lower() != "result.txt"
                and not child.name.startswith(".")
            )
            if not found:
                raise FileNotFoundError(f"資料夾裡沒有可轉換的 txt：{path}")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"找不到檔案：{path}")
    if not files:
        raise FileNotFoundError("沒有指定任何輸入檔")
    return files


def convert(inputs: list[Path], output: Path, append: bool) -> list[tuple[Path, list[str]]]:
    converted = [(path, codes_from_save(read_text(path))) for path in inputs]
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = read_text(output) if append and output.exists() else ""
    number = next_student_number(existing) if existing else 1
    blocks = []
    for _path, codes in converted:
        blocks.append(session_block(number, codes))
        number += 1
    payload = "".join(blocks)
    if append and existing:
        with output.open("a", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        output.write_text(payload, encoding="utf-8")
    return converted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mac 版資料轉換（取代 MPS.exe）。每個 txt 變成一段 % Student #。"
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="save.txt 或含有 save 紀錄的資料夾")
    parser.add_argument("-o", "--output", type=Path, default=Path("result.txt"), help="輸出檔，預設 ./result.txt")
    parser.add_argument("--append", action="store_true", help="接到既有 result.txt 後面，並延續學生編號")
    parser.add_argument("--self-test", action="store_true", help="用素材包裡的 save.txt 核對轉換規則")
    return parser


def expected_demo_codes() -> str:
    return "A B C D D D D E F F F I J G G G G F G F G F F T T T"


def run_self_test() -> int:
    package = Path(__file__).resolve().parent.parent
    sample = package / "01.資料轉換" / "save.txt"
    if not sample.is_file():
        print(f"找不到自我測試檔：{sample}", file=sys.stderr)
        return 1
    codes = codes_from_save(read_text(sample))
    got = " ".join(codes)
    if got != expected_demo_codes():
        print("自我測試失敗。", file=sys.stderr)
        print(f"期望：{expected_demo_codes()}", file=sys.stderr)
        print(f"得到：{got}", file=sys.stderr)
        return 1
    print(f"自我測試通過：{sample.name} → {len(codes)} 個代碼")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.inputs:
        print("請指定至少一個 save.txt 或資料夾。", file=sys.stderr)
        return 1
    try:
        inputs = collect_inputs(args.inputs)
        converted = convert(inputs, args.output, args.append)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"讀寫失敗：{exc}", file=sys.stderr)
        return 1

    print(f"已寫入 {args.output}")
    for path, codes in converted:
        preview = " ".join(codes) if codes else "（沒有 A–Z 開頭的行）"
        print(f"  {path.name}: {len(codes)} 個代碼  {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
