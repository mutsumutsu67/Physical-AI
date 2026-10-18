#!/usr/bin/env python3
"""eval_full.py - 指示10パターン × N回 を通しで実行して成功率を測る（評価用）

前提: Gazebo / skill_executor / llm_planner（/instruction→/plan）が起動済み
    cd /workspace/pai_kitchen/nodes && python3 eval_full.py --trials 3
結果: /workspace/data/eval_full.csv（1試行1行）と、画面に集計表
"""
import argparse
import csv
import json
import time

import rclpy
from std_msgs.msg import String

from eval_planner import CASES

OUT = "/workspace/data/eval_full.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--only", nargs="*", help="指示の一部を含むケースだけ実行（例: --only お辞儀 いらっしゃい）")
    args = ap.parse_args()

    rclpy.init()
    node = rclpy.create_node("eval_full")
    pub = node.create_publisher(String, "/instruction", 10)
    box = {"plan": None, "done": None}
    node.create_subscription(String, "/plan", lambda m: box.__setitem__("plan", json.loads(m.data)), 10)

    def on_status(m):
        d = json.loads(m.data)
        # この指示の計画を受け取った後の通知だけを採用（前の指示の遅れた通知を無視）
        if box["plan"] is not None and d.get("state") in ("done", "rejected"):
            box["done"] = d
    node.create_subscription(String, "/skill_status", on_status, 10)
    time.sleep(1.0)

    rows = []
    for trial in range(1, args.trials + 1):
        for cat, text, expect in CASES:
            if args.only and not any(k in text for k in args.only):
                continue
            box["plan"], box["done"] = None, None
            t0 = time.time()
            pub.publish(String(data=text))
            while time.time() - t0 < args.timeout:
                rclpy.spin_once(node, timeout_sec=0.1)
                if box["done"] or (box["plan"] and box["plan"].get("rejected")):
                    break
            sec = round(time.time() - t0, 1)
            plan = box["plan"] or {}
            got = None if plan.get("rejected") else [s["skill"] for s in plan.get("steps", [])]
            plan_ok = (got == expect) or (expect and got and got[:-1] == expect and got[-1] == "bow")
            res = (box["done"] or {}).get("results", [])
            exec_ok = (expect is None and plan.get("rejected")) or (bool(res) and all(r["ok"] for r in res))
            rows.append([trial, cat, text, expect, got, "○" if plan_ok else "×", "○" if exec_ok else "×",
                         f"{sum(r['ok'] for r in res)}/{len(res)}" if res else "-", sec])
            print(f"[{trial}] {'○' if plan_ok else '×'}{'○' if exec_ok else '×'} {text}  計画={got}  実行={rows[-1][7]}  {sec}s")
            # 遅れて届く /skill_status（拒否通知など）を読み捨ててから次へ（前の結果の混入防止）
            t1 = time.time()
            while time.time() - t1 < 2.0:
                rclpy.spin_once(node, timeout_sec=0.1)

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["試行", "区分", "指示", "期待", "計画", "計画判定", "実行判定", "スキル成功", "秒"])
        w.writerows(rows)
    n = len(rows)
    print(f"\n計画の正解率 {sum(r[5] == '○' for r in rows)}/{n}　通しの成功率 {sum(r[6] == '○' for r in rows)}/{n}　→ {OUT}")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
