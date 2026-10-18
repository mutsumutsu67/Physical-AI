#!/usr/bin/env python3
"""llm_planner.py - 自然言語の指示を OpenAI でスキル列に分解し、検証して /plan に出す

単体テスト（ROS 不要、API キーのみ必要）:
    python3 llm_planner.py --text "カレーを混ぜて、塩を振って、できたら出して"
ROS ノードとして起動:
    python3 llm_planner.py            # /instruction を購読 → /plan を publish

API キー: /workspace/pai_kitchen/.env の OPENAI_API_KEY（環境変数でも可）
モデル  : 環境変数 OPENAI_MODEL（既定 gpt-4o-mini）
"""
import argparse
import json
import os
import sys
import time

from kitchen_skills import SKILLS, MAX_STEPS, skill_catalog_text, validate_plan, reject_plan

ENV_PATH = os.environ.get("PAI_ENV", "/workspace/pai_kitchen/.env")
LOG_PATH = os.environ.get("PAI_PLAN_LOG", "/workspace/data/plan_log.jsonl")


def load_env(path=ENV_PATH):
    """python-dotenv なしで .env を読む（KEY=VALUE 形式）"""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


SYSTEM_PROMPT = f"""あなたはキッチンで働く調理補助ロボットアームの計画係です。
利用者の日本語の指示を、下の登録スキルだけを使った手順に分解してください。

登録スキル:
{skill_catalog_text()}

規則:
- 使えるのは上の skill 名だけ。新しい名前を作らない。
- 指示の順番どおりに並べる。最大 {MAX_STEPS} ステップ。
- 「いらっしゃいませ」「こんにちは」などの挨拶が文頭にあれば、まず bow から始める。
- 「できたら」「最後に」などで料理を出す指示があれば serve を使い、最後に bow を付けてよい。
- 登録スキルで実現できない指示（移動・飛ぶ・物を持ち上げて運ぶ・危険な行為など）は rejected=true にし、steps は空、理由を reject_reason に書く。
- say は各ステップでロボットが言う短い日本語（20 文字以内）。reply は実行前の返事なので「〜します」の形（30 文字以内）。拒否するときも reply に一言お詫びを書く。
- 例:
  「いらっしゃいませ、塩は左にあります」→ bow, point_left
  「こんにちは、調味料は右です」→ bow, point_right
  「混ぜてから出して」→ stir, serve, bow
- 出力は次の JSON オブジェクトのみ:
{{"steps":[{{"skill":"<名前>","say":"<短い発話>"}}],"reply":"<返事>","rejected":false,"reject_reason":""}}
"""


class Planner:
    def __init__(self, model=None):
        load_env()
        from openai import OpenAI          # pip install --user openai
        # br/zstd 圧縮を避ける（コンテナの古い brotli だと httpx の展開で TypeError になるため）
        self.client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def _ask(self, messages):
        res = self.client.chat.completions.create(
            model=self.model, messages=messages,
            response_format={"type": "json_object"}, temperature=0)
        return res.choices[0].message.content

    def plan(self, instruction):
        """戻り値: (plan dict, info dict)"""
        t0 = time.time()
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction}]
        tries = []
        for attempt in range(2):          # 不正なら 1 回だけ作り直させる
            try:
                raw = self._ask(messages)
            except Exception as e:        # 通信エラー等
                tries.append({"error": str(e)})
                break
            ok, plan, errs = validate_plan(raw)
            tries.append({"raw": raw, "ok": ok, "errors": errs})
            if ok:
                break
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "出力に誤りがあります: " + "; ".join(errs) + "。規則に従って JSON を出し直してください。"}]
        else:
            plan = reject_plan("計画の検証に2回失敗")
        if not tries or "error" in tries[-1]:
            plan = reject_plan("LLM に接続できない")
        info = {"instruction": instruction, "model": self.model, "attempts": len(tries),
                "latency_s": round(time.time() - t0, 2), "tries": tries, "plan": plan,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(info, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return plan, info


def run_ros():
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class PlannerNode(Node):
        def __init__(self):
            super().__init__("llm_planner")
            self.planner = Planner()
            self.pub = self.create_publisher(String, "/plan", 10)
            self.create_subscription(String, "/instruction", self.on_instruction, 10)
            self.get_logger().info(f"ready (model={self.planner.model})  /instruction → /plan")

        def on_instruction(self, msg):
            text = msg.data.strip()
            if not text:
                return
            self.get_logger().info(f"指示: {text}")
            plan, info = self.planner.plan(text)
            self.get_logger().info(f"計画: {json.dumps(plan, ensure_ascii=False)} ({info['latency_s']}s, {info['attempts']}回)")
            self.pub.publish(String(data=json.dumps(plan, ensure_ascii=False)))

    rclpy.init()
    node = PlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="ROS を使わずに 1 回だけ計画して表示")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    if args.text:
        plan, info = Planner(args.model).plan(args.text)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print(f"-- model={info['model']} latency={info['latency_s']}s attempts={info['attempts']}", file=sys.stderr)
    else:
        run_ros()


if __name__ == "__main__":
    main()
