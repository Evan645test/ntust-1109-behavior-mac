#!/usr/bin/env python3
"""事件序列的 lag-1 行為轉換分析，對應課堂上 GSEQ 的 simple statistics。"""

from __future__ import annotations

import argparse
import html
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

Z_CUTOFF = 1.96
DECLARED_RE = re.compile(r"\(\s*\$[^=]*=\s*([^)]*)\)")
HEADER_LINE_RE = re.compile(r"^(event|timed|state|interval)\s*$", re.IGNORECASE)
TYPE_LINE_RE = re.compile(r"^type\b", re.IGNORECASE)


@dataclass(frozen=True)
class Session:
    label: str
    codes: list[str]
    source: str


@dataclass(frozen=True)
class Cell:
    given: str
    target: str
    observed: int
    expected: float
    probability: float | None
    z: float | None
    q: float | None

    @property
    def significant(self) -> bool:
        return self.z is not None and abs(self.z) >= Z_CUTOFF


@dataclass(frozen=True)
class Table:
    title: str
    codes: list[str]
    cells: dict[tuple[str, str], Cell]
    transitions: int
    events: int
    dropped_repeats: bool

    def cell(self, given: str, target: str) -> Cell:
        return self.cells[(given, target)]


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


def declared_codes(text: str) -> list[str]:
    found: list[str] = []
    for match in DECLARED_RE.finditer(text):
        for token in match.group(1).split():
            if token not in found:
                found.append(token)
    return found


def _strip_declarations(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped.startswith("(") or stripped.startswith("*"):
            continue
        if HEADER_LINE_RE.match(stripped) or TYPE_LINE_RE.match(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def parse_sessions(text: str, source: str) -> list[Session]:
    body = _strip_declarations(text)
    sessions: list[Session] = []
    for chunk in body.split(";"):
        label = ""
        codes: list[str] = []
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("%"):
                label = line.lstrip("%").strip() or label
                continue
            ended = False
            for token in line.split():
                if token in {"=", "$", "/"} or token.startswith("(") or token.startswith("$"):
                    if token == "/":
                        ended = True
                    continue
                if token.endswith("/"):
                    token = token[:-1]
                    ended = True
                if token:
                    codes.append(token)
                if ended:
                    break
        if codes or label:
            sessions.append(Session(label=label, codes=codes, source=source))
    return [session for session in sessions if session.codes]


def prepare_sequence(codes: list[str], keep: set[str] | None, drop_repeats: bool) -> list[str]:
    sequence = [code for code in codes if keep is None or code in keep]
    if not drop_repeats:
        return sequence
    collapsed: list[str] = []
    for code in sequence:
        if not collapsed or collapsed[-1] != code:
            collapsed.append(code)
    return collapsed


def yule_q(observed: int, row: int, column: int, total: int) -> float | None:
    a = observed
    b = row - a
    c = column - a
    d = total - a - b - c
    if min(a, b, c, d) < 0:
        return None
    denominator = a * d + b * c
    if denominator == 0:
        return None
    return (a * d - b * c) / denominator


def analyze(sessions: list[Session], codes: list[str], drop_repeats: bool, title: str) -> Table:
    keep = set(codes)
    counts: Counter[tuple[str, str]] = Counter()
    event_count = 0
    for session in sessions:
        sequence = prepare_sequence(session.codes, keep, drop_repeats)
        event_count += len(sequence)
        counts.update(zip(sequence, sequence[1:]))

    total = sum(counts.values())
    rows = {given: sum(counts[(given, target)] for target in codes) for given in codes}
    columns = {target: sum(counts[(given, target)] for given in codes) for target in codes}
    cells: dict[tuple[str, str], Cell] = {}
    for given in codes:
        for target in codes:
            observed = counts[(given, target)]
            if total == 0 or rows[given] == 0 or columns[target] == 0:
                expected = 0.0
                probability = (observed / rows[given]) if rows[given] else None
                z_value = None
                q_value = None
            else:
                expected = rows[given] * columns[target] / total
                probability = observed / rows[given]
                row_p = rows[given] / total
                column_p = columns[target] / total
                variance = expected * (1 - row_p) * (1 - column_p)
                z_value = (observed - expected) / math.sqrt(variance) if variance > 0 else None
                q_value = yule_q(observed, rows[given], columns[target], total)
            cells[(given, target)] = Cell(
                given=given,
                target=target,
                observed=observed,
                expected=expected,
                probability=probability,
                z=z_value,
                q=q_value,
            )
    return Table(
        title=title,
        codes=codes,
        cells=cells,
        transitions=total,
        events=event_count,
        dropped_repeats=drop_repeats,
    )


def order_codes(found: list[str], declared: list[str], requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    unique: list[str] = []
    for code in found:
        if code not in unique:
            unique.append(code)
    if declared:
        head = [code for code in declared if code in unique]
        tail = sorted(code for code in unique if code not in head)
        return head + tail
    if unique and all(len(code) == 1 for code in unique):
        return sorted(unique)
    return unique


def parse_code_argument(value: str) -> list[str]:
    parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
    if len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9]+", parts[0]) and parts[0].isalpha():
        return list(parts[0])
    if not parts:
        raise argparse.ArgumentTypeError("代碼表是空的")
    return parts


def load_inputs(paths: list[Path]) -> tuple[list[Session], list[str]]:
    sessions: list[Session] = []
    declared: list[str] = []
    files = collect_inputs(paths)
    for path in files:
        text = read_text(path)
        for code in declared_codes(text):
            if code not in declared:
                declared.append(code)
        sessions.extend(parse_sessions(text, path.name))
    if not sessions:
        raise ValueError("這些檔案裡沒有事件序列。請改用 student.txt 或 MPS 產出的 result.txt。")
    return sessions, declared


def collect_inputs(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted(
                child
                for child in path.iterdir()
                if child.is_file() and child.suffix.lower() == ".txt" and not child.name.startswith(".")
            )
            if not found:
                raise FileNotFoundError(f"資料夾裡沒有 txt：{path}")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"找不到檔案：{path}")
    return files


def fmt(value: float | None, digits: int) -> str:
    if value is None or math.isnan(value):
        return "—"
    return f"{value:.{digits}f}"


def significant_pairs(table: Table) -> list[Cell]:
    pairs = [table.cell(given, target) for given in table.codes for target in table.codes]
    return sorted(
        (cell for cell in pairs if cell.significant),
        key=lambda cell: abs(cell.z or 0),
        reverse=True,
    )


def matrix_csv(table: Table, value: str) -> str:
    header = ",".join(["given\\target", *table.codes])
    lines = [header]
    for given in table.codes:
        row = [given]
        for target in table.codes:
            cell = table.cell(given, target)
            if value == "observed":
                row.append(str(cell.observed))
            elif value == "expected":
                row.append(fmt(cell.expected, 4))
            elif value == "probability":
                row.append("" if cell.probability is None else f"{cell.probability:.6f}")
            else:
                row.append("" if cell.z is None else f"{cell.z:.6f}")
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"


def text_report(tables: list[Table], sessions: list[Session]) -> str:
    lines: list[str] = []
    lines.append("行為轉換分析（lag 1，GSEQ simple statistics）")
    lines.append(f"序列數：{len(sessions)}")
    for session in sessions:
        name = session.label or session.source
        lines.append(f"  - {name}（{session.source}）：{len(session.codes)} 個事件")
    lines.append("")
    lines.append("調整後殘差 z = (觀察次數 − 期望次數) / 開根號[期望次數 × (1 − 列比例) × (1 − 行比例)]")
    lines.append(f"|z| ≥ {Z_CUTOFF:.2f} 視為雙尾 α = .05 顯著。正值代表這個轉換比獨立時更常出現。")
    lines.append("期望次數 = 該列總和 × 該行總和 / 轉換總數。轉換不跨過分號。")
    lines.append("")
    for table in tables:
        lines.append("=" * 72)
        lines.append(table.title)
        repeat = "已去掉連續重複" if table.dropped_repeats else "保留連續重複"
        lines.append(f"{repeat}。事件 {table.events}，轉換 {table.transitions}。")
        lines.append("代碼：" + " ".join(table.codes))
        lines.append("")
        lines.append("觀察次數")
        lines.extend(_text_matrix(table, lambda cell: str(cell.observed)))
        lines.append("")
        lines.append("條件機率 P(後|前)")
        lines.extend(_text_matrix(table, lambda cell: fmt(cell.probability, 3)))
        lines.append("")
        lines.append("期望次數")
        lines.extend(_text_matrix(table, lambda cell: fmt(cell.expected, 2)))
        lines.append("")
        lines.append("調整後殘差 z")
        lines.extend(_text_matrix(table, lambda cell: fmt(cell.z, 2)))
        lines.append("")
        hits = significant_pairs(table)
        if hits:
            lines.append("顯著轉換")
            for cell in hits:
                direction = "高於期望" if (cell.z or 0) > 0 else "低於期望"
                lines.append(
                    f"  {cell.given} → {cell.target}  "
                    f"z={fmt(cell.z, 2)}  次數={cell.observed}  期望={fmt(cell.expected, 2)}  "
                    f"P={fmt(cell.probability, 3)}  Q={fmt(cell.q, 3)}  {direction}"
                )
        else:
            lines.append("沒有達到顯著的轉換。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _text_matrix(table: Table, render) -> list[str]:
    width = 8
    header = " " * width + "".join(f"{code:>{width}}" for code in table.codes)
    rows = [header]
    for given in table.codes:
        values = "".join(f"{render(table.cell(given, target)):>{width}}" for target in table.codes)
        rows.append(f"{given:>{width}}{values}")
    return rows


def html_report(tables: list[Table], sessions: list[Session]) -> str:
    sections = "\n".join(render_section(table) for table in tables)
    session_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(session.label or "（未命名）"),
            html.escape(session.source),
            len(session.codes),
        )
        for session in sessions
    )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>行為轉換報告</title>
<style>
  :root {{
    --ink: #1c1915;
    --muted: #5c564c;
    --line: #e4dccf;
    --paper: #fffdf8;
    --page: #efe6d6;
    --pos: #9a3412;
    --neg: #1e3a5f;
    --pos-bg: #fde8dc;
    --neg-bg: #e4ebf5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page);
    color: var(--ink);
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", "Songti TC", serif;
  }}
  main {{
    width: min(1120px, calc(100% - 32px));
    margin: 28px auto 64px;
  }}
  header.page {{
    padding: 8px 0 20px;
    border-bottom: 1px solid var(--line);
    margin-bottom: 28px;
  }}
  h1 {{
    margin: 0 0 8px;
    font-size: 34px;
    font-weight: 600;
    letter-spacing: 0.01em;
  }}
  h2 {{
    margin: 0 0 8px;
    font-size: 26px;
  }}
  h3 {{
    margin: 22px 0 8px;
    font-size: 18px;
  }}
  p {{ line-height: 1.55; }}
  .lede {{ color: var(--muted); max-width: 68ch; }}
  section {{
    background: var(--paper);
    border: 1px solid var(--line);
    padding: 22px 22px 28px;
    margin: 18px 0;
    overflow-x: auto;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: "Helvetica Neue", "PingFang TC", sans-serif;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }}
  th, td {{
    border-bottom: 1px solid var(--line);
    padding: 6px 8px;
    text-align: right;
  }}
  @media (max-width: 760px) {{
    main {{ width: calc(100% - 12px); margin-top: 12px; }}
    section {{ padding: 14px 8px 18px; }}
    table {{ font-size: 10px; }}
    th, td {{ padding: 4px 2px; }}
  }}
  th:first-child, td:first-child {{ text-align: left; white-space: nowrap; }}
  th {{ color: var(--muted); font-weight: 600; }}
  td.pos {{ background: var(--pos-bg); color: var(--pos); font-weight: 700; }}
  td.neg {{ background: var(--neg-bg); color: var(--neg); font-weight: 700; }}
  .meta {{ color: var(--muted); font-family: "Helvetica Neue", "PingFang TC", sans-serif; font-size: 14px; }}
  .diagram {{ width: 100%; height: auto; }}
  ul.hits {{ padding-left: 1.2em; }}
  ul.hits li {{ margin: 4px 0; }}
  .formula {{
    font-family: "Helvetica Neue", "PingFang TC", sans-serif;
    font-size: 14px;
    background: #f7f1e6;
    padding: 10px 12px;
  }}
</style>
</head>
<body>
<main>
  <header class="page">
    <h1>行為轉換報告</h1>
    <p class="lede">lag 1 事件序列。同一段裡相鄰兩個代碼算一次轉換，分號之後另起一段，不把上一段的最後一個代碼連到下一段。</p>
    <p class="formula">z = (觀察次數 − 期望次數) / √[期望次數 × (1 − 列比例) × (1 − 行比例)]。|z| ≥ 1.96 著色，雙尾 α = .05。這是 GSEQ simple statistics 的 adjusted residual。</p>
  </header>
  <section>
    <h2>序列</h2>
    <table>
      <thead><tr><th>名稱</th><th>檔案</th><th>事件數</th></tr></thead>
      <tbody>
        {session_rows}
      </tbody>
    </table>
  </section>
  {sections}
</main>
</body>
</html>
"""


def render_section(table: Table) -> str:
    repeat = "已去掉連續相同代碼" if table.dropped_repeats else "保留連續相同代碼（行為持續時會重複計）"
    hits = significant_pairs(table)
    if hits:
        items = "\n".join(
            "<li><strong>{} → {}</strong>　z = {}　次數 {}（期望 {}）　條件機率 {}　Yule's Q {}　{}</li>".format(
                html.escape(cell.given),
                html.escape(cell.target),
                fmt(cell.z, 2),
                cell.observed,
                fmt(cell.expected, 2),
                fmt(cell.probability, 3),
                fmt(cell.q, 3),
                "高於期望" if (cell.z or 0) > 0 else "低於期望",
            )
            for cell in hits
        )
        hit_html = f"<ul class=\"hits\">{items}</ul>"
    else:
        hit_html = "<p>沒有達到顯著的轉換。</p>"
    return f"""
<section>
  <h2>{html.escape(table.title)}</h2>
  <p class="meta">{html.escape(repeat)}。事件 {table.events}，轉換 {table.transitions}。代碼：{html.escape(" ".join(table.codes))}</p>
  <h3>轉換圖</h3>
  {render_diagram(table)}
  <h3>顯著轉換</h3>
  {hit_html}
  <h3>調整後殘差 z</h3>
  {render_matrix(table, lambda cell: fmt(cell.z, 2), color=True)}
  <h3>觀察次數</h3>
  {render_matrix(table, lambda cell: str(cell.observed), color=False)}
  <h3>條件機率 P(後|前)</h3>
  {render_matrix(table, lambda cell: fmt(cell.probability, 3), color=False)}
  <h3>期望次數</h3>
  {render_matrix(table, lambda cell: fmt(cell.expected, 2), color=False)}
</section>
"""


def render_matrix(table: Table, render, color: bool) -> str:
    head = "".join(f"<th>{html.escape(code)}</th>" for code in table.codes)
    rows: list[str] = []
    for given in table.codes:
        cells: list[str] = [f"<td>{html.escape(given)}</td>"]
        for target in table.codes:
            cell = table.cell(given, target)
            css = ""
            if color and cell.significant:
                css = " pos" if (cell.z or 0) > 0 else " neg"
            cells.append(f"<td class=\"{css.strip()}\">{html.escape(render(cell))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table><thead><tr><th>前 \\ 後</th>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_diagram(table: Table) -> str:
    if table.transitions == 0 or not table.codes:
        return "<p>沒有足夠的轉換可以畫圖。</p>"
    width, height = 860, 760
    cx, cy = width / 2, height / 2
    radius = 210
    positions: dict[str, tuple[float, float]] = {}
    angles: dict[str, float] = {}
    count = len(table.codes)
    for index, code in enumerate(table.codes):
        angle = -math.pi / 2 + (2 * math.pi * index / count)
        angles[code] = angle
        positions[code] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    marker_suffix = f"m{abs(hash(table.title)) % 100000}"
    shown = [cell for cell in significant_pairs(table) if (cell.z or 0) > 0]
    if not shown:
        return "<p>沒有高於期望的顯著轉換。低於期望的轉換留在下面的表。</p>"
    arrows: list[str] = []
    for cell in shown:
        if cell.given == cell.target:
            arrows.append(_self_loop(positions[cell.given], angles[cell.given], cell, marker_suffix))
        else:
            arrows.append(_arrow(positions[cell.given], positions[cell.target], cell, marker_suffix))
    defs = (
        "<defs>"
        f'<marker id="arrow-pos-{marker_suffix}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 1.2 L 10 5 L 0 8.8 z" fill="#9a3412"/></marker>'
        f'<marker id="arrow-neg-{marker_suffix}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 1.2 L 10 5 L 0 8.8 z" fill="#1e3a5f"/></marker>'
        "</defs>"
    )
    nodes = []
    for code, (x, y) in positions.items():
        nodes.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="26" fill="#fffdf8" stroke="#1c1915" stroke-width="1.4"/>'
            f'<text x="{x:.1f}" y="{y + 5:.1f}" text-anchor="middle" font-size="18" '
            f'font-family="Palatino, Songti TC, serif">{html.escape(code)}</text>'
        )
    return (
        '<p class="meta">圖只畫高於期望、且 |z| ≥ 1.96 的轉換。低於期望的留在下面的表，避免線疊在一起。線上的數字是 z。</p>'
        f'<svg class="diagram" viewBox="0 0 {width} {height}" role="img" aria-label="高於期望的顯著轉換">'
        + defs
        + "".join(arrows)
        + "".join(nodes)
        + "</svg>"
    )


def _arrow(start: tuple[float, float], end: tuple[float, float], cell: Cell, marker_suffix: str) -> str:
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1
    ux, uy = dx / length, dy / length
    gap = 34
    sx, sy = x1 + ux * gap, y1 + uy * gap
    ex, ey = x2 - ux * gap, y2 - uy * gap
    bend = 28 if (cell.z or 0) > 0 else -28
    mx, my = (sx + ex) / 2 - uy * bend, (sy + ey) / 2 + ux * bend
    positive = (cell.z or 0) > 0
    color = "#9a3412" if positive else "#1e3a5f"
    marker = f"arrow-pos-{marker_suffix}" if positive else f"arrow-neg-{marker_suffix}"
    dash = "" if positive else ' stroke-dasharray="5 4"'
    width = 1.2 + min(abs(cell.z or 0), 8) * 0.28
    return (
        f'<path d="M {sx:.1f} {sy:.1f} Q {mx:.1f} {my:.1f} {ex:.1f} {ey:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{width:.2f}"{dash} marker-end="url(#{marker})"/>'
        f'<text x="{mx:.1f}" y="{my:.1f}" font-size="12" fill="{color}" text-anchor="middle" '
        f'font-family="Helvetica Neue, sans-serif" stroke="#fffdf8" stroke-width="4" paint-order="stroke">{fmt(cell.z, 2)}</text>'
    )


def _self_loop(origin: tuple[float, float], angle: float, cell: Cell, marker_suffix: str) -> str:
    x, y = origin
    outward_x, outward_y = math.cos(angle), math.sin(angle)
    tangent_x, tangent_y = -outward_y, outward_x
    positive = (cell.z or 0) > 0
    color = "#9a3412" if positive else "#1e3a5f"
    marker = f"arrow-pos-{marker_suffix}" if positive else f"arrow-neg-{marker_suffix}"
    dash = "" if positive else ' stroke-dasharray="5 4"'
    width = 1.2 + min(abs(cell.z or 0), 8) * 0.22

    def point(tangent: float, outward: float) -> tuple[float, float]:
        return (
            x + tangent_x * tangent + outward_x * outward,
            y + tangent_y * tangent + outward_y * outward,
        )

    start = point(14, 22)
    end = point(-14, 22)
    control_a = point(48, 86)
    control_b = point(-48, 86)
    label = point(0, 98)
    return (
        f'<path d="M {start[0]:.1f} {start[1]:.1f} C {control_a[0]:.1f} {control_a[1]:.1f}, '
        f'{control_b[0]:.1f} {control_b[1]:.1f}, {end[0]:.1f} {end[1]:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{width:.2f}"{dash} marker-end="url(#{marker})"/>'
        f'<text x="{label[0]:.1f}" y="{label[1]:.1f}" font-size="12" fill="{color}" text-anchor="middle" '
        f'font-family="Helvetica Neue, sans-serif" stroke="#fffdf8" stroke-width="4" paint-order="stroke">{fmt(cell.z, 2)}</text>'
    )


def write_outputs(output: Path, tables: list[Table], sessions: list[Session]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_report(tables, sessions), encoding="utf-8")
    text_path = output.with_suffix(".txt")
    text_path.write_text(text_report(tables, sessions), encoding="utf-8")
    primary = tables[0]
    stem = output.stem
    (output.parent / f"{stem}-z.csv").write_text("\ufeff" + matrix_csv(primary, "z"), encoding="utf-8")
    (output.parent / f"{stem}-次數.csv").write_text(
        "\ufeff" + matrix_csv(primary, "observed"), encoding="utf-8"
    )
    (output.parent / f"{stem}-條件機率.csv").write_text(
        "\ufeff" + matrix_csv(primary, "probability"), encoding="utf-8"
    )


def build_tables(
    sessions: list[Session],
    declared: list[str],
    requested: list[str] | None,
    drop_repeats: bool,
) -> list[Table]:
    found = [code for session in sessions for code in session.codes]
    codes = order_codes(found, declared, requested)
    if not codes:
        raise ValueError("代碼表是空的")
    missing = [code for code in codes if code not in set(found)]
    if requested and missing and set(found).isdisjoint(codes):
        raise ValueError("指定的代碼都沒有出現在序列裡：" + " ".join(codes))
    title = "全部序列合併"
    if requested:
        title += "（代碼 " + "".join(codes) + "）" if all(len(code) == 1 for code in codes) else "（指定代碼）"
    tables = [analyze(sessions, codes, drop_repeats, title)]
    extra_codes = list("ABCDEFG")
    observed = set(found)
    if (
        requested is None
        and not drop_repeats
        and set(extra_codes) <= observed
        and observed - set(extra_codes)
    ):
        tables.append(
            analyze(
                sessions,
                extra_codes,
                False,
                "只保留 A–G（講義轉換圖的 4.09 用的是這張表）",
            )
        )
    return tables


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mac 版行為轉換分析（取代課堂上的 GSEQ lag-1 表與轉換圖）。")
    parser.add_argument("inputs", nargs="*", type=Path, help="GSEQ 序列 txt，或 MPS 產出的 result.txt")
    parser.add_argument("-o", "--output", type=Path, default=Path("行為轉換報告.html"))
    parser.add_argument(
        "--codes",
        type=parse_code_argument,
        help="只分析這些代碼。連續字母如 ABCDEFG 會拆成單碼；也可用逗號分隔。",
    )
    parser.add_argument("--drop-repeats", action="store_true", help="先去掉連續重複代碼，再計算轉換")
    parser.add_argument("--self-test", action="store_true", help="用素材包六位學生核對 D→D 的 z 是否為 4.09")
    return parser


def run_self_test() -> int:
    package = Path(__file__).resolve().parent.parent
    folder = package / "02.數據資料" / "02.GSEQ(數據包)"
    students = sorted(folder.glob("student*.txt"))
    if len(students) < 6:
        print(f"自我測試找不到六個 student 檔：{folder}", file=sys.stderr)
        return 1
    sessions, _declared = load_inputs(students)
    table = analyze(sessions, list("ABCDEFG"), drop_repeats=False, title="self-test")
    z_value = table.cell("D", "D").z
    if z_value is None or round(z_value, 2) != 4.09:
        print(f"自我測試失敗：D→D z = {z_value}", file=sys.stderr)
        return 1
    print(f"自我測試通過：六位學生、代碼 A–G、D→D z = {z_value:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.inputs:
        print("請指定至少一個序列檔或資料夾。", file=sys.stderr)
        return 1
    try:
        sessions, declared = load_inputs(args.inputs)
        tables = build_tables(sessions, declared, args.codes, args.drop_repeats)
        write_outputs(args.output, tables, sessions)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"已寫入 {args.output}")
    print(f"已寫入 {args.output.with_suffix('.txt')}")
    print(f"已寫入 {args.output.parent / (args.output.stem + '-z.csv')}")
    primary = tables[0]
    for cell in significant_pairs(primary)[:8]:
        print(f"  {cell.given} → {cell.target}  z={fmt(cell.z, 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
