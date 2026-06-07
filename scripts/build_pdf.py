"""Генерира пълен PDF доклад от markdown, метрики и графики."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

FONT_REGULAR = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
FONT_BOLD = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf"
FONT_MONO = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSansMono.ttf"

DOCS = ROOT / "docs"
REPORTS = DOCS / "reports"
FIGURES = DOCS / "figures"
MD_PATH = DOCS / "ДОКЛАД_ФИНАЛ.md"
OUT_PDF = DOCS / "Доклад_Препоръчваща_система.pdf"


def _load_json(tag: str) -> dict | None:
    path = REPORTS / f"{tag}_metrics.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_inline(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def _parse_blocks(md: str) -> list[tuple[str, str]]:
    """Връща (тип, съдържание): h1, h2, h3, p, ul, ol, table, code, hr, metrics."""
    lines = md.splitlines()
    blocks: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.strip() == "---":
            blocks.append(("hr", ""))
            i += 1
            continue
        if line.startswith("# ") and not line.startswith("## "):
            blocks.append(("h1", _clean_inline(line[2:])))
            i += 1
            continue
        if line.startswith("## "):
            blocks.append(("h2", _clean_inline(line[3:])))
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(("h3", _clean_inline(line[4:])))
            i += 1
            continue
        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(table_lines)))
            continue
        if line.startswith("    ") or line.startswith("\t"):
            code_lines = []
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].startswith("\t")):
                code_lines.append(lines[i].lstrip())
                i += 1
            blocks.append(("code", "\n".join(code_lines)))
            continue
        if line.strip().startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(_clean_inline(lines[i].strip()[2:]))
                i += 1
            blocks.append(("ul", "\n".join(items)))
            continue
        if re.match(r"^\d+\.\s", line.strip()):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append(_clean_inline(re.sub(r"^\d+\.\s*", "", lines[i].strip())))
                i += 1
            blocks.append(("ol", "\n".join(items)))
            continue
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
            if lines[i].startswith("|") or lines[i].strip().startswith("- "):
                break
            if re.match(r"^\d+\.\s", lines[i].strip()):
                break
            if lines[i].startswith("    "):
                break
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            blocks.append(("p", _clean_inline(" ".join(para_lines))))
        else:
            i += 1
    return blocks


def _metrics_table_block(payload: dict, title: str) -> str:
    rows = payload.get("rows", [])
    stats = payload.get("stats", {})
    header = "Метод|P@K|R@K|NDCG@K|O"
    sep = "---|---|---|---|---"
    body_lines = []
    for r in rows:
        body_lines.append(
            f"{r['method']}|{r['precision_at_k']:.4f}|{r['recall_at_k']:.4f}|"
            f"{r['ndcg_at_k']:.4f}|{r['overall']:.4f}"
        )
    meta = (
        f"Версия {payload['tag']} | K={payload['k']} | "
        f"потребители={stats.get('users', '?')} | книги={stats.get('books', '?')} | "
        f"fold={payload.get('users_in_fold', '?')}"
    )
    return f"META:{title}\n{meta}\n{header}\n{sep}\n" + "\n".join(body_lines)


def _w(pdf) -> float:
    return pdf.epw


def _ensure_space(pdf, needed: float = 20) -> None:
    if pdf.get_y() + needed > pdf.page_break_trigger:
        pdf.add_page()


def _render_table(pdf, table_text: str) -> None:
    from fpdf.enums import XPos, YPos

    lines = [ln for ln in table_text.split("\n") if ln.strip()]
    if not lines:
        return
    if lines[0].startswith("META:"):
        pdf.set_font("DejaVu", "B", 10)
        pdf.multi_cell(_w(pdf), 6, lines[0].replace("META:", "").strip())
        pdf.set_font("DejaVu", "", 9)
        pdf.multi_cell(_w(pdf), 5, lines[1])
        pdf.ln(2)
        lines = lines[2:]

    data_rows = []
    for line in lines:
        if line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        data_rows.append(cells)
    if not data_rows:
        return

    col_n = len(data_rows[0])
    col_w = _w(pdf) / col_n
    pdf.set_font("DejaVu", "B", 8)
    for cell in data_rows[0]:
        pdf.cell(col_w, 7, cell[:28], border=1)
    pdf.ln()
    pdf.set_font("DejaVu", "", 8)
    for row in data_rows[1:]:
        _ensure_space(pdf, 8)
        for j, cell in enumerate(row):
            w = col_w
            pdf.cell(w, 7, cell[:28], border=1)
        pdf.ln()
    pdf.ln(3)


def build_pdf() -> Path:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    if not MD_PATH.is_file():
        raise FileNotFoundError(f"Липсва {MD_PATH}")

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("DejaVu", "", str(FONT_REGULAR))
    pdf.add_font("DejaVu", "B", str(FONT_BOLD))
    if FONT_MONO.is_file():
        pdf.add_font("DejaVuMono", "", str(FONT_MONO))

    # ── Корица ──
    pdf.add_page()
    pdf.ln(35)
    pdf.set_font("DejaVu", "B", 22)
    pdf.multi_cell(_w(pdf), 12, "Препоръчваща система за книги", align="C")
    pdf.ln(6)
    pdf.set_font("DejaVu", "", 13)
    pdf.multi_cell(_w(pdf), 8, "Документация и резултати от offline оценка", align="C")
    pdf.ln(4)
    pdf.set_font("DejaVu", "", 11)
    pdf.multi_cell(_w(pdf), 7, "Курсов проект — Препоръчващи системи", align="C")
    pdf.multi_cell(_w(pdf), 7, "Факултет по математика и информатика", align="C")
    pdf.ln(10)
    pdf.multi_cell(_w(pdf), 7, "Никол Николаева, Габриела Костева", align="C")
    pdf.ln(20)
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(
        _w(pdf),
        6,
        "Hybrid препоръчване с content-based filtering, collaborative filtering, "
        "popularity, social signal и MMR. Оценка: leave-last-out, Precision@K, Recall@K, NDCG@K.",
        align="C",
    )

    v0 = _load_json("v0")
    v1 = _load_json("v1")
    v2 = _load_json("v2")
    v3 = _load_json("v3")
    v4 = _load_json("v4")

    # ── Съдържание ──
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(_w(pdf), 10, "Съдържание", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)
    toc = [
        "1. Въведение",
        "2. Функционалности на системата",
        "3. Архитектура и източници на данни",
        "4. Алгоритми",
        "5. Методи за оценяване",
        "6. Експеримент: еволюция на данните",
        "7. Резултати от оценката",
        "8. Графики",
        "9. Извод",
        "10. Стартиране и възпроизвеждане",
    ]
    pdf.set_font("DejaVu", "", 11)
    for item in toc:
        pdf.cell(_w(pdf), 8, item, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    md = MD_PATH.read_text(encoding="utf-8")
    blocks = _parse_blocks(md)
    metrics_inserted = False

    for kind, content in blocks:
        if kind == "hr":
            pdf.ln(2)
            continue

        if kind == "h1":
            continue  # вече на корицата

        if kind == "h2":
            if content.startswith("7. Резултати"):
                pdf.add_page()
                pdf.set_font("DejaVu", "B", 15)
                pdf.multi_cell(_w(pdf), 9, content)
                pdf.ln(2)
                if v0:
                    _render_table(pdf, _metrics_table_block(v0, "Таблица 1 — Базова линия (v0)"))
                if v1:
                    _render_table(pdf, _metrics_table_block(v1, "Таблица 2 — След подобрения на каталога (v1)"))
                if v2:
                    _render_table(pdf, _metrics_table_block(v2, "Таблица 3 — Синтетични данни, вълна 1 (v2)"))
                if v3:
                    _render_table(pdf, _metrics_table_block(v3, "Таблица 4 — Синтетични данни, вълна 2 (v3)"))
                if v4:
                    _render_table(pdf, _metrics_table_block(v4, "Таблица 5 — След hyperparameter tuning (v4)"))
                metrics_inserted = True
                continue
            if content.startswith("8. Графики"):
                continue  # графиките са в отделна секция
            _ensure_space(pdf, 25)
            if pdf.get_y() > 40:
                pdf.ln(4)
            pdf.set_font("DejaVu", "B", 14)
            pdf.multi_cell(_w(pdf), 9, content)
            pdf.ln(2)
            continue

        if kind == "h3":
            _ensure_space(pdf, 18)
            pdf.set_font("DejaVu", "B", 11)
            pdf.multi_cell(_w(pdf), 7, content)
            pdf.ln(1)
            continue

        if kind == "p":
            pdf.set_font("DejaVu", "", 10)
            pdf.multi_cell(_w(pdf), 5.5, content)
            pdf.ln(2)
            continue

        if kind == "ul":
            pdf.set_font("DejaVu", "", 10)
            for item in content.split("\n"):
                if item.strip():
                    pdf.multi_cell(_w(pdf), 5.5, f"  •  {item}")
            pdf.ln(2)
            continue

        if kind == "ol":
            pdf.set_font("DejaVu", "", 10)
            for n, item in enumerate(content.split("\n"), 1):
                if item.strip():
                    pdf.multi_cell(_w(pdf), 5.5, f"  {n}.  {item}")
            pdf.ln(2)
            continue

        if kind == "table":
            _render_table(pdf, content)
            continue

        if kind == "code":
            _ensure_space(pdf, 15)
            pdf.set_font("DejaVuMono" if FONT_MONO.is_file() else "DejaVu", "", 9)
            for cl in content.split("\n"):
                pdf.multi_cell(_w(pdf), 5, cl)
            pdf.ln(3)
            continue

    if not metrics_inserted and v2:
        pdf.add_page()
        pdf.set_font("DejaVu", "B", 14)
        pdf.cell(_w(pdf), 9, "7. Резултати от оценката", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        if v0:
            _render_table(pdf, _metrics_table_block(v0, "Таблица 1 — Базова линия (v0)"))
        if v2:
            _render_table(pdf, _metrics_table_block(v2, "Таблица 2 — Финал (v2)"))

    # ── Графики ──
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 15)
    pdf.cell(_w(pdf), 10, "8. Графики", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(
        _w(pdf),
        5.5,
        "Offline метрики при K=10. Индекс O = (Precision@K + Recall@K + NDCG@K) / 3.",
    )
    pdf.ln(2)

    figure_order = [
        ("evolution_hybrid_social.png", "Фиг. 1 — Еволюция Hybrid+Social (v0→v4)"),
        ("evolution_hybrid.png", "Фиг. 1b — Еволюция Hybrid (v0→v4)"),
        ("evolution_all_methods.png", "Фиг. 2 — Еволюция на всички методи по версии"),
        ("comparison_baseline_vs_final.png", "Фиг. 3 — Сравнение v0 vs v4"),
        ("v4_overall_score.png", "Фиг. 4 — Общ резултат (след tuning, v4)"),
        ("v4_metrics_grouped.png", "Фиг. 5 — Precision / Recall / NDCG (v4)"),
        ("v4_radar.png", "Фиг. 6 — Radar chart (v4)"),
        ("v3_overall_score.png", "Фиг. 7 — Преди tuning (v3)"),
    ]
    for fname, caption in figure_order:
        fpath = FIGURES / fname
        if not fpath.is_file():
            continue
        pdf.add_page()
        pdf.set_font("DejaVu", "B", 11)
        pdf.multi_cell(_w(pdf), 7, caption)
        pdf.ln(3)
        pdf.image(str(fpath), x=20, w=_w(pdf))

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT_PDF))
    return OUT_PDF


def main() -> None:
    path = build_pdf()
    print(f"pdf={path}")


if __name__ == "__main__":
    main()
