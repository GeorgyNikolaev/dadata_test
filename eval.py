"""Оценка качества на размеченной выборке data/eval.csv.

Режимы (абляция):
  no_llm   — только детерминированная проверка: ИНН/ОГРН найден на сайте -> принять;
  no_retry — LLM-вердикт + правило, без второй итерации поиска;
  full     — полный пайплайн (LLM-вердикт + LLM-генерация новых запросов).

Метрики по основному домену (domain):
  precision = верных доменов / всех возвращённых доменов;
  recall    = верных доменов / организаций, у которых сайт есть;
  null_acc  = доля верных null среди организаций без сайта;
  accuracy  = доля ИНН с полностью верным ответом (домен из эталона или верный null).

Справочник отключён: каждый ИНН ищется заново. Внешние ответы кэшируются,
поэтому повторный прогон и сравнение режимов почти ничего не стоят.

Запуск: uv run python eval.py [--data data/holdout.csv] [--modes full,no_retry,no_llm]
data/eval.csv — выборка для разработки (на ней разбирались ошибки), data/holdout.csv — отложенная.
"""
import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path

from site_finder import config
from site_finder.pipeline import find_site

ROOT = Path(__file__).parent
MODES = {
    "no_llm": dict(use_llm=False, max_iterations=1),
    "no_retry": dict(use_llm=True, max_iterations=1),
    "full": dict(use_llm=True, max_iterations=2),
}


def load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["inn"] and not r["inn"].startswith("#")]
    for r in rows:
        r["truth"] = {d.strip().lower() for d in r["expected"].split(";") if d.strip()}
    return rows


def outcome(pred: str | None, truth: set[str]) -> str:
    """TP — верный домен; FN — сайт есть, вернули null; TN — сайта нет, вернули null;
    FP_wrong — сайт есть, вернули чужой; FP_nosite — сайта нет, вернули домен."""
    if pred is None:
        return "TN" if not truth else "FN"
    if pred in truth:
        return "TP"
    return "FP_wrong" if truth else "FP_nosite"


def metrics(outcomes: list[str]) -> dict:
    n = defaultdict(int)
    for o in outcomes:
        n[o] += 1
    fp = n["FP_wrong"] + n["FP_nosite"]
    returned = n["TP"] + fp
    with_site = n["TP"] + n["FN"] + n["FP_wrong"]
    without_site = n["TN"] + n["FP_nosite"]
    return {
        "n": len(outcomes),
        "precision": n["TP"] / returned if returned else None,
        "recall": n["TP"] / with_site if with_site else None,
        "null_acc": n["TN"] / without_site if without_site else None,
        "accuracy": (n["TP"] + n["TN"]) / len(outcomes) if outcomes else None,
        "TP": n["TP"], "FP": fp, "FN": n["FN"], "TN": n["TN"],
    }


def fmt(x) -> str:
    return "—" if x is None else f"{x:.2f}" if isinstance(x, float) else str(x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "eval.csv"))
    ap.add_argument("--modes", default="full,no_retry,no_llm")
    args = ap.parse_args()

    data = Path(args.data)
    rows = load(data)
    results = []
    logs_root = config.LOGS_DIR
    for mode in args.modes.split(","):
        config.LOGS_DIR = logs_root / data.stem / mode
        for r in rows:
            t0 = time.time()
            try:
                res = find_site(r["inn"], directory=None, **MODES[mode])
            except Exception as e:  # оценка не должна падать из-за одного ИНН
                print(f"[{mode}] {r['inn']}: ошибка {e!r}")
                res = {"domain": None, "sites": [], "error": repr(e)}
            o = outcome(res["domain"], r["truth"])
            trace = config.LOGS_DIR / f"{r['inn']}.json"
            t = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else {}
            results.append({
                "mode": mode, "inn": r["inn"], "segment": r["segment"], "expected": r["expected"],
                "domain": res["domain"] or "", "sites": ";".join(res["sites"]), "outcome": o,
                "llm_tokens": t.get("llm_tokens", 0), "iterations": len(t.get("iterations", [])),
                "seconds": round(time.time() - t0, 1),
            })
            print(f"[{mode}] {r['inn']} {r['segment']:<10} {o}  pred={res['domain']}  expected={r['expected'] or 'null'}")

    out = data.with_name(f"{data.stem}_results.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)

    # Сводные таблицы: по режимам и по сегментам (для полного режима).
    cols = ["n", "precision", "recall", "null_acc", "accuracy", "TP", "FP", "FN", "TN"]
    print("\n| режим | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1))
    for mode in args.modes.split(","):
        m = metrics([x["outcome"] for x in results if x["mode"] == mode])
        print(f"| {mode} | " + " | ".join(fmt(m[c]) for c in cols) + " |")

    main_mode = args.modes.split(",")[0]
    print(f"\nПо сегментам ({main_mode}):\n| сегмент | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1))
    for seg in sorted({x["segment"] for x in results}):
        m = metrics([x["outcome"] for x in results if x["mode"] == main_mode and x["segment"] == seg])
        print(f"| {seg} | " + " | ".join(fmt(m[c]) for c in cols) + " |")
    print(f"\nДетали: {out.relative_to(ROOT)}, трассировки: logs/{data.stem}/<режим>/<ИНН>.json")


if __name__ == "__main__":
    main()
