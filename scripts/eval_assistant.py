#!/usr/bin/env python3
"""Run the assistant core locally without Telegram or external writes."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from assistant.engine import AssistantEngine  # noqa: E402
from assistant.models import TurnRequest, TurnResult  # noqa: E402
from llm.gemini_client import gemini_client  # noqa: E402
from llm.tool_executor import DryRunToolExecutor  # noqa: E402


RESULT_COLUMNS = [
    "case_id",
    "turn",
    "user_message",
    "expected_tool",
    "actual_tool",
    "passed",
    "model_path",
    "reply_text",
    "handover_reason",
    "dry_run",
    "model_ms",
    "tool_ms",
    "total_ms",
    "total_tokens",
    "error",
    "manual_score",
    "manual_notes",
]


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    dataset_defaults: dict[str, Any] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: некорректный JSON") from exc
            if not isinstance(case, dict):
                raise ValueError(f"{path}:{line_number}: кейс должен быть объектом")
            if "_meta" in case:
                metadata = case["_meta"]
                if not isinstance(metadata, dict):
                    raise ValueError(f"{path}:{line_number}: _meta должен быть объектом")
                dataset_defaults = {
                    "manual_score": metadata.get("manual_score_default", ""),
                    "manual_notes": metadata.get("manual_notes_default", ""),
                }
                continue
            if "turns" not in case:
                message = case.get("message")
                if not message:
                    raise ValueError(f"{path}:{line_number}: нет turns или message")
                case["turns"] = [
                    {
                        "user": message,
                        "expected_tool": case.get("expected_tool"),
                    }
                ]
            for turn_number, turn in enumerate(case["turns"], start=1):
                for key, value in dataset_defaults.items():
                    if value not in (None, ""):
                        turn.setdefault(key, value)
                score = turn.get("manual_score")
                if score in (None, ""):
                    continue
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    raise ValueError(
                        f"{path}:{line_number}: manual_score хода "
                        f"{turn_number} должен быть числом от 1 до 5"
                    )
                if not 1 <= float(score) <= 5:
                    raise ValueError(
                        f"{path}:{line_number}: manual_score хода "
                        f"{turn_number} должен быть от 1 до 5"
                    )
            cases.append(case)
    return cases


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _expected_tools(turn: dict[str, Any]) -> list[str]:
    """Return all semantically acceptable routes for an eval turn."""
    alternatives = turn.get("expected_tools")
    if alternatives:
        if isinstance(alternatives, str):
            return [alternatives]
        return [str(value) for value in alternatives]
    expected = turn.get("expected_tool")
    return [str(expected)] if expected else []


def _check_turn(turn: dict[str, Any], result: TurnResult) -> bool:
    checks: list[bool] = []
    expected_tools = _expected_tools(turn)
    if expected_tools:
        checks.append(result.tool_name in expected_tools)

    expected_handover = turn.get("expected_handover")
    if expected_handover is not None:
        checks.append(bool(result.handover_reason) is bool(expected_handover))

    reply = result.reply_text or ""
    contains = turn.get("reply_contains") or []
    if isinstance(contains, str):
        contains = [contains]
    checks.extend(str(fragment) in reply for fragment in contains)

    excludes = turn.get("reply_not_contains") or []
    if isinstance(excludes, str):
        excludes = [excludes]
    checks.extend(str(fragment) not in reply for fragment in excludes)

    checks.append(result.error is None)
    return all(checks)


def _row(
    case_id: str,
    turn_number: int,
    turn: dict[str, Any],
    result: TurnResult,
) -> dict[str, Any]:
    trace = result.trace
    trace_data = trace.as_dict() if trace else {}
    return {
        "case_id": case_id,
        "turn": turn_number,
        "user_message": turn.get("user", ""),
        "expected_tool": "|".join(_expected_tools(turn)),
        "actual_tool": result.tool_name or "",
        "passed": _check_turn(turn, result),
        "model_path": trace_data.get("model_path", ""),
        "reply_text": result.reply_text or "",
        "handover_reason": result.handover_reason or "",
        "dry_run": result.dry_run,
        "model_ms": trace_data.get("model_ms", ""),
        "tool_ms": trace_data.get("tool_ms", ""),
        "total_ms": trace_data.get("total_ms", ""),
        "total_tokens": trace_data.get("total_tokens") or "",
        "error": result.error or "",
        "manual_score": turn.get("manual_score", ""),
        "manual_notes": turn.get("manual_notes", ""),
    }


async def _run_dataset(
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    engine = AssistantEngine(
        router=gemini_client,
        executor=DryRunToolExecutor(),
        metrics_enabled=False,
    )
    for case_index, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or f"case_{case_index}")
        history = list(case.get("history") or [])
        user_context = {
            "telegram_id": str(case.get("telegram_id") or f"eval-{case_index}"),
            "first_name": str(case.get("first_name") or "Eval"),
            "username": case.get("username") or "eval_user",
            "dialog_link": "",
        }
        for turn_number, turn in enumerate(case["turns"], start=1):
            message = str(turn.get("user") or "")
            result = await engine.process_turn(
                TurnRequest(
                    message=message,
                    history=history,
                    user_context=user_context,
                )
            )
            rows.append(_row(case_id, turn_number, turn, result))
            history.append({"role": "user", "content": message})
            if result.reply_text:
                history.append({"role": "assistant", "content": result.reply_text})
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["total_ms"]) for row in rows if row["total_ms"] != ""]
    expected_rows = [row for row in rows if row["expected_tool"]]
    manual_scores = [
        float(row["manual_score"])
        for row in rows
        if row.get("manual_score") not in (None, "")
    ]
    return {
        "generated_at": datetime.now().isoformat(),
        "model": rows[0]["model_path"] if rows else "",
        "turns": len(rows),
        "passed": sum(bool(row["passed"]) for row in rows),
        "route_accuracy": (
            sum(
                row["actual_tool"] in row["expected_tool"].split("|")
                for row in expected_rows
            )
            / len(expected_rows)
            if expected_rows
            else None
        ),
        "errors": sum(bool(row["error"]) for row in rows),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0,
            "p50": round(statistics.median(latencies), 2) if latencies else 0,
            "p95": round(_percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "total_tokens": sum(
            int(row["total_tokens"])
            for row in rows
            if row["total_tokens"] != ""
        ),
        "manual_review": {
            "reviewed": len(manual_scores),
            "mean_score": (
                round(statistics.fmean(manual_scores), 2) if manual_scores else None
            ),
            "accepted": sum(score >= 4 for score in manual_scores),
            "acceptance_rate": (
                round(
                    sum(score >= 4 for score in manual_scores)
                    / len(manual_scores),
                    4,
                )
                if manual_scores
                else None
            ),
        },
    }


def _print_rows(rows: list[dict[str, Any]], *, metrics: bool) -> None:
    columns = ["case_id", "turn", "actual_tool", "passed", "model_path"]
    if metrics:
        columns.extend(["total_ms", "total_tokens"])
    widths = {column: len(column) for column in columns}
    display_rows: list[dict[str, str]] = []
    for row in rows:
        display = {}
        for column in columns:
            value = str(row.get(column, ""))
            display[column] = value if len(value) <= 42 else f"{value[:39]}..."
            widths[column] = max(widths[column], len(display[column]))
        display_rows.append(display)

    header = " | ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("-+-".join("-" * widths[column] for column in columns))
    for row in display_rows:
        print(" | ".join(row[column].ljust(widths[column]) for column in columns))


def _write_results(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    metrics: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "results.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in rows:
            written = dict(row)
            if not metrics:
                for key in (
                    "model_ms",
                    "tool_ms",
                    "total_ms",
                    "total_tokens",
                ):
                    written[key] = ""
            writer.writerow(written)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(_summary(rows), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nCSV: {csv_path}")
    print(f"Summary: {summary_path}")


async def _interactive(metrics: bool) -> None:
    engine = AssistantEngine(
        router=gemini_client,
        executor=DryRunToolExecutor(),
        metrics_enabled=False,
    )
    history: list[dict[str, str]] = []
    context = {
        "telegram_id": "eval-interactive",
        "first_name": "Eval",
        "username": "eval_user",
        "dialog_link": "",
    }
    print("Интерактивный eval. Введите /exit для выхода.")
    while True:
        try:
            message = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if message.lower() in {"/exit", "/quit"}:
            return
        if not message:
            continue
        result = await engine.process_turn(
            TurnRequest(message=message, history=history, user_context=context)
        )
        print(f"Ассистент: {result.reply_text or '[нет ответа]'}")
        print(f"Tool: {result.tool_name or '—'}")
        if result.handover_reason:
            print(f"Handover: {result.handover_reason}")
        if result.error:
            print(f"Error: {result.error}")
        if metrics and result.trace:
            print(json.dumps(result.trace.as_dict(), ensure_ascii=False))
        history.append({"role": "user", "content": message})
        if result.reply_text:
            history.append({"role": "assistant", "content": result.reply_text})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--metrics",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.interactive and not args.dataset:
        parser.error("укажите --interactive или --dataset")
    if args.interactive and args.dataset:
        parser.error("--interactive и --dataset взаимоисключающие")
    return args


async def _main() -> None:
    args = _parse_args()
    if not os.getenv("GEMINI_API_KEY"):
        print("Предупреждение: GEMINI_API_KEY не задан; модельные вызовы завершатся ошибкой.")

    if args.interactive:
        await _interactive(args.metrics)
        return

    cases = _load_cases(args.dataset)
    rows = await _run_dataset(cases)
    _print_rows(rows, metrics=args.metrics)
    output_dir = args.output_dir or (
        PROJECT_ROOT / "eval_runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    _write_results(rows, output_dir, metrics=args.metrics)


if __name__ == "__main__":
    asyncio.run(_main())
