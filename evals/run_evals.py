"""评测框架示例 (M7): 回归评测 CI 集成.

在 CI 中运行: python evals/run_evals.py --threshold 0.85
分数低于阈值则拦截合并 (保护 prompt/模型变更不引入退化).
"""
from __future__ import annotations

import argparse
import json

CASES = [
    {"id": "qa-001", "q": "公司差旅报销标准是多少?", "expect_contains": ["报销"], "doc_acl": "all"},
    {"id": "qa-002", "q": "Q3 财报净利润", "expect_contains": ["净利润"], "doc_acl": "finance"},
]


def evaluate(case: dict, answer: str) -> float:
    hits = sum(1 for kw in case["expect_contains"] if kw in answer)
    return hits / len(case["expect_contains"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--report", default="eval_report.json")
    args = parser.parse_args()

    scores = []
    for case in CASES:
        answer = "[mock] " + " ".join(case["expect_contains"])  # 实际调用 orchestrator
        score = evaluate(case, answer)
        scores.append({"id": case["id"], "score": score})
        print(f"{case['id']}: {score:.2f}")

    avg = sum(s["score"] for s in scores) / len(scores)
    json.dump({"average": avg, "cases": scores}, open(args.report, "w"), ensure_ascii=False, indent=2)
    passed = avg >= args.threshold
    print(f"average={avg:.2f} threshold={args.threshold} -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
