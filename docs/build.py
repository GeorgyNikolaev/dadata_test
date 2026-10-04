"""Сборка PDF с ответом: docs/answer.html + docs/task2.html + таблицы метрик -> docs/answer.pdf.

Таблицы считаются из data/<выборка>_results.csv (результат eval.py), PDF печатает headless Chrome.
Запуск: uv run python docs/build.py --repo https://github.com/<user>/<repo>
"""
import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval import metrics  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MODE_NAMES = {"full": "полный пайплайн", "no_retry": "без 2-й итерации", "no_llm": "без LLM (только ИНН/ОГРН на сайте)"}
SEGMENTS = {
    "large": "крупные компании", "small_legal": "малые/средние ЮЛ с сайтом", "ip_site": "ИП с сайтом",
    "random_legal": "случайные ЮЛ", "random_ip": "случайные ИП", "liquidated": "ликвидированные",
}


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def load(name: str) -> list[dict]:
    with open(ROOT / "data" / f"{name}_results.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def modes_table(rows: list[dict]) -> str:
    out = ["<table><tr><th>Режим</th><th>precision</th><th>recall</th><th>верные null</th><th>accuracy</th>"
           "<th>TP</th><th>FP</th><th>FN</th><th>TN</th><th>с/ИНН*</th><th>токенов/ИНН</th></tr>"]
    for mode in ("full", "no_retry", "no_llm"):
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        m = metrics([r["outcome"] for r in rs])
        sec = sum(float(r["seconds"]) for r in rs) / len(rs)
        tok = sum(int(r["llm_tokens"]) for r in rs) / len(rs)
        out.append(f"<tr><td>{MODE_NAMES[mode]}</td><td class=n>{pct(m['precision'])}</td><td class=n>{pct(m['recall'])}</td>"
                   f"<td class=n>{pct(m['null_acc'])}</td><td class=n>{pct(m['accuracy'])}</td>"
                   f"<td class=n>{m['TP']}</td><td class=n>{m['FP']}</td><td class=n>{m['FN']}</td><td class=n>{m['TN']}</td>"
                   f"<td class=n>{sec:.0f}</td><td class=n>{tok:.0f}</td></tr>")
    out.append("</table>")
    return "".join(out)


def segments_table(rows: list[dict]) -> str:
    rs = [r for r in rows if r["mode"] == "full"]
    out = ["<table><tr><th>Сегмент</th><th>n</th><th>precision</th><th>recall</th><th>верные null</th>"
           "<th>TP</th><th>FP</th><th>FN</th><th>TN</th></tr>"]
    for seg, title in SEGMENTS.items():
        sr = [r for r in rs if r["segment"] == seg]
        if not sr:
            continue
        m = metrics([r["outcome"] for r in sr])
        out.append(f"<tr><td>{title}</td><td class=n>{m['n']}</td><td class=n>{pct(m['precision'])}</td>"
                   f"<td class=n>{pct(m['recall'])}</td><td class=n>{pct(m['null_acc'])}</td><td class=n>{m['TP']}</td>"
                   f"<td class=n>{m['FP']}</td><td class=n>{m['FN']}</td><td class=n>{m['TN']}</td></tr>")
    out.append("</table>")
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()

    dev, hold = load("eval"), load("holdout")
    combined = [r for r in dev + hold]
    analysis = (ROOT / "docs" / "eval_analysis.html").read_text(encoding="utf-8")
    eval_html = analysis.format(
        dev_n=len({r["inn"] for r in dev}), hold_n=len({r["inn"] for r in hold}),
        dev_modes=modes_table(dev), hold_modes=modes_table(hold), segments=segments_table(combined),
    )
    task2 = (ROOT / "docs" / "task2.html").read_text(encoding="utf-8").replace("{{EVAL}}", eval_html)
    t1_evidence = (ROOT / "docs" / "t1_evidence.html").read_text(encoding="utf-8").strip()
    html = ((ROOT / "docs" / "answer.html").read_text(encoding="utf-8")
            .replace("{{TASK2}}", task2).replace("{{T1_EVIDENCE}}", t1_evidence).replace("{{REPO_URL}}", args.repo))
    out_html = ROOT / "docs" / "answer_full.html"
    out_html.write_text(html, encoding="utf-8")
    pdf = ROOT / "docs" / "answer.pdf"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", out_html.as_uri()], check=True, capture_output=True)
    print(pdf)


if __name__ == "__main__":
    main()
