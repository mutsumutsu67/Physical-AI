#!/usr/bin/env python3
"""skill_executor.py - /plan のスキル列を、スキル別 ACT 方策で順に実行する

起動（pixi 環境で。lerobot / torch が必要）:
    cd ~/pai_training && pixi run python /workspace/pai_kitchen/nodes/skill_executor.py
単体テスト（計画なしで 1 スキルだけ）:
    cd ~/pai_training && pixi run python /workspace/pai_kitchen/nodes/skill_executor.py --skill stir

流れ（1 ステップごと）:
    HOMING  直立へスクリプトで復帰（学習時と同じ初期状態にする）
    RUNNING ACT で 20Hz 推論（policy.reset() でチャンクを毎回クリア）
    終了判定 関節がほぼ止まった状態が STILL_SEC 続く or タイムアウト
    → 次のステップ → 全部終われば直立へ戻して IDLE

関節の並び（重要）:
    観測 state  : pai_training の infer.py と同じ [joint1, joint2, joint4, joint3, hand]
    行動 action : record_demo.py が記録した順   [joint1, joint2, joint3, joint4] + hand
                  （infer.py は行動を [1,2,4,3] として送るため、joint3 と joint4 が入れ替わる）
"""
import argparse
import collections
import json
import os
import time

import numpy as np
import rclpy
import torch
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from lerobot.common.policies.act.modeling_act import ACTPolicy

SKILLS = ["bow", "point_left", "point_right", "serve", "stir", "shake"]
CKPT = "/workspace/data/checkpoints/kitchen_{}_act"

STATE_JOINTS = ["crane_plus_joint1", "crane_plus_joint2", "crane_plus_joint4", "crane_plus_joint3"]
ACTION_JOINTS = ["crane_plus_joint1", "crane_plus_joint2", "crane_plus_joint3", "crane_plus_joint4"]
HAND = "crane_plus_joint_hand"
ARM_TOPIC = "/crane_plus_arm_controller/joint_trajectory"
GRIP_TOPIC = "/crane_plus_gripper_controller/joint_trajectory"

RATE = 20.0
HOME_TIMEOUT = 5.0      # 直立復帰の最大待ち [s]
HOME_TOL = 0.03         # 直立とみなす誤差 [rad]
MIN_RUN = 1.5           # これより早くは終了判定しない [s]
STILL_SEC = 0.6         # この時間ほぼ動かなければ終了 [s]
STILL_TOL = {"bow": 0.03}   # 「ほぼ動かない」の閾値 [rad]（既定 0.01）。bow は前傾姿勢で重力による微振動が残るため緩める
HOME_SETTLE = 2.3           # 直立指令（2 秒軌道）を出してから最低これだけ待つ [s]
VERSION = "executor v3 (still_tol per skill, home settle)"
TIMEOUT = {"stir": 14.0, "shake": 10.0, "serve": 8.0}   # 既定 7 秒
LOG_PATH = "/workspace/data/exec_log.jsonl"


class SkillExecutor(Node):
    def __init__(self, single_skill=None, swap_action=True):
        super().__init__("skill_executor")
        self.arm_pub = self.create_publisher(JointTrajectory, ARM_TOPIC, 10)
        self.grip_pub = self.create_publisher(JointTrajectory, GRIP_TOPIC, 10)
        self.status_pub = self.create_publisher(String, "/skill_status", 10)
        self.create_subscription(JointState, "/joint_states", self.on_js, 10)
        self.create_subscription(String, "/plan", self.on_plan, 10)
        self.action_joints = ACTION_JOINTS if swap_action else STATE_JOINTS

        self.policies = {}
        for s in SKILLS:
            path = CKPT.format(s)
            if os.path.isdir(path):
                p = ACTPolicy.from_pretrained(path)
                p.eval()
                p.to(torch.device("cpu"))
                self.policies[s] = p
        self.get_logger().info(f"{VERSION} / loaded policies: {list(self.policies)}")

        self.pos = {}                       # joint name -> position
        self.history = collections.deque()  # (t, np.array) 終了判定用
        self.queue, self.plan, self.plan_id = [], None, 0
        self.state, self.step_idx, self.skill, self.t0 = "IDLE", -1, None, 0.0
        self.results = []
        if single_skill:
            self.start_plan({"steps": [{"skill": single_skill, "say": single_skill}],
                             "reply": f"test {single_skill}", "rejected": False})
        self.create_timer(1.0 / RATE, self.tick)

    # ------------------------------------------------------------ 入力
    def on_js(self, msg):
        self.pos.update(dict(zip(msg.name, msg.position)))

    def on_plan(self, msg):
        try:
            plan = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().error("plan が JSON ではない")
            return
        if self.state != "IDLE":
            self.get_logger().warn("実行中のため新しい計画は無視しました")
            return
        if plan.get("rejected"):
            self.publish_status("rejected", extra={"reason": plan.get("reject_reason", "")})
            return
        self.start_plan(plan)

    def start_plan(self, plan):
        steps = [s for s in plan.get("steps", []) if s.get("skill") in self.policies]
        if not steps:
            self.get_logger().warn("実行できるステップがありません")
            return
        self.plan_id += 1
        self.plan, self.queue, self.results = plan, list(steps), []
        self.step_idx = -1
        self.get_logger().info(f"計画 #{self.plan_id}: {[s['skill'] for s in steps]}")
        self.next_step()

    # ------------------------------------------------------------ 出力
    def send(self, arm, grip, dt, names):
        def traj(n, p):
            m = JointTrajectory()
            m.joint_names = n
            pt = JointTrajectoryPoint()
            pt.positions = [float(x) for x in p]
            pt.time_from_start.sec = int(dt)
            pt.time_from_start.nanosec = int((dt - int(dt)) * 1e9)
            m.points = [pt]
            return m
        self.arm_pub.publish(traj(names, arm))
        self.grip_pub.publish(traj([HAND], [grip]))

    def publish_status(self, state, extra=None):
        steps = [s["skill"] for s in (self.plan or {}).get("steps", [])]
        d = {"plan_id": self.plan_id, "index": self.step_idx, "skill": self.skill,
             "state": state, "steps": steps, "say": self.current_say(),
             "time": round(time.time(), 2)}
        if extra:
            d.update(extra)
        self.status_pub.publish(String(data=json.dumps(d, ensure_ascii=False)))
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def current_say(self):
        try:
            return self.plan["steps"][self.step_idx].get("say", "")
        except (TypeError, IndexError, KeyError):
            return ""

    # ------------------------------------------------------------ 状態遷移
    def next_step(self):
        if self.queue:
            st = self.queue.pop(0)
            self.step_idx += 1
            self.skill = st["skill"]
            self.enter("HOMING")
        else:
            self.skill = None
            self.enter("FINAL_HOMING")

    def enter(self, state):
        self.state, self.t0 = state, time.time()
        self.history.clear()
        if state in ("HOMING", "FINAL_HOMING"):
            self.send([0.0] * 4, 0.0, 2.0, ACTION_JOINTS)
        if state == "RUNNING":
            self.policies[self.skill].reset()      # 行動チャンクのキューを空にする
            self.publish_status("running")
            self.get_logger().info(f"▶ {self.step_idx + 1}. {self.skill}")

    def state_vec(self):
        arm = [self.pos.get(j, 0.0) for j in STATE_JOINTS]
        return np.array(arm + [self.pos.get(HAND, 0.0)], dtype=np.float32)

    def tick(self):
        if not self.pos:
            return
        now = time.time()
        el = now - self.t0

        if self.state in ("HOMING", "FINAL_HOMING"):
            err = max(abs(self.pos.get(j, 0.0)) for j in ACTION_JOINTS)
            if (err < HOME_TOL and el > HOME_SETTLE) or el > HOME_TIMEOUT:
                if self.state == "HOMING":
                    self.enter("RUNNING")
                else:
                    ok = sum(r["ok"] for r in self.results)
                    self.publish_status("done", extra={"results": self.results})
                    self.get_logger().info(f"■ 計画 #{self.plan_id} 完了 ({ok}/{len(self.results)})")
                    self.state, self.plan = "IDLE", None
            return

        if self.state != "RUNNING":
            return

        s = self.state_vec()
        obs = {"observation.state": torch.from_numpy(s).unsqueeze(0),
               "observation.environment_state": torch.from_numpy(s).unsqueeze(0)}
        with torch.no_grad():
            a = self.policies[self.skill].select_action(obs).cpu().numpy().flatten()
        self.send(a[:4], a[4], 0.1, self.action_joints)

        # 終了判定：直近 STILL_SEC の関節変化が小さい
        self.history.append((now, s[:4].copy()))
        while self.history and now - self.history[0][0] > STILL_SEC:
            self.history.popleft()
        span = now - self.history[0][0] if self.history else 0
        still = False
        if el > MIN_RUN and span > STILL_SEC * 0.9:
            arr = np.stack([h[1] for h in self.history])
            still = float((arr.max(0) - arr.min(0)).max()) < STILL_TOL.get(self.skill, 0.01)
        timeout = el > TIMEOUT.get(self.skill, 7.0)
        if still or timeout:
            res = {"skill": self.skill, "ok": bool(still), "sec": round(el, 2),
                   "end": "still" if still else "timeout"}
            self.results.append(res)
            self.publish_status("finished", extra=res)
            self.get_logger().info(f"  {'✔' if still else '⏱'} {self.skill} {res['sec']}s ({res['end']})")
            self.next_step()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", choices=SKILLS, help="計画なしで 1 スキルだけ実行")
    ap.add_argument("--no-swap", action="store_true",
                    help="行動を infer.py と同じ [1,2,4,3] の並びで送る（比較用）")
    args = ap.parse_args()
    rclpy.init()
    node = SkillExecutor(args.skill, swap_action=not args.no_swap)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
