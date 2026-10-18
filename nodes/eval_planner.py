#!/usr/bin/env python3
"""eval_planner.py - 評価用の指示10パターンで計画だけを一括テスト（ロボット不要）
    python3 eval_planner.py            # 結果を表で表示し、/workspace/data/eval_planner.csv に保存
"""
import csv
from llm_planner import Planner

CASES = [  # (区分, 指示, 期待するスキル列 / None は拒否)
    ("単一", "お辞儀して", ["bow"]),
    ("単一", "右を指して", ["point_right"]),
    ("単一", "塩を振って", ["shake"]),
    ("複合", "カレーを混ぜて、塩を少し振って、できたら出して", ["stir", "shake", "serve", "bow"]),
    ("複合", "混ぜてから出して", ["stir", "serve"]),
    ("複合", "胡椒を振ってから鍋をかき混ぜて", ["shake", "stir"]),
    ("複合", "いらっしゃいませ、塩は左にあります", ["bow", "point_left"]),
    ("言い換え", "ぐるぐるかき混ぜておいて", ["stir"]),
    ("言い換え", "調味料ってどこ？右？", ["point_right"]),
    ("拒否", "空を飛んで", None),
]


def main():
    p = Planner()
    rows, ok_n = [], 0
    for cat, text, expect in CASES:
        plan, info = p.plan(text)
        got = None if plan["rejected"] else [s["skill"] for s in plan["steps"]]
        if expect and expect[-1] != "bow" and got and got[-1] == "bow":
            got_cmp = got[:-1]            # 末尾の bow 追加は許容
        else:
            got_cmp = got
        ok = (got_cmp == expect) or (got == expect)
        ok_n += ok
        rows.append([cat, text, expect, got, "○" if ok else "×", info["latency_s"], plan["reply"]])
        print(f"{'○' if ok else '×'} [{cat}] {text}\n    期待={expect}  結果={got}  ({info['latency_s']}s) {plan['reply']}")
    print(f"\n計画の正解率: {ok_n}/{len(CASES)}")
    with open("/workspace/data/eval_planner.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["区分", "指示", "期待", "結果", "判定", "秒", "返事"])
        w.writerows(rows)


if __name__ == "__main__":
    main()
