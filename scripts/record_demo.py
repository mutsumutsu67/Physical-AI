#!/usr/bin/env python3
"""
record_demo.py - CRANE+ V2 キッチンスキルのデモ収集補助

  script モード : 軌道生成スクリプトがアームを動かし、それを rosbag に記録（ノイズ付き）
  teleop モード : teleop_keyboard で人が操作し、記録の開始/停止と採番だけを自動化

使い方（コンテナ内、Gazebo 起動済みの状態で）:
  # 見た目確認（記録しない）
  python3 record_demo.py --skill stir --dry-run
  # スクリプトで15エピソード収集
  python3 record_demo.py --skill stir --episodes 15
  # 人のテレオペで収集（別ターミナルで teleop_keyboard を起動しておく）
  python3 record_demo.py --skill bow --mode teleop

設計メモ（レポートの工夫点）:
  - 観測は関節角のみ（state-only ACT）。そのため
    (1) 開始姿勢(HOME)と終了姿勢を必ず別にする（始まりと終わりを区別できるように）
    (2) 周期動作(stir/shake)は位置をドリフトさせ、同じ状態が二度現れないようにする
  - 記録開始直後に動かす（ease-out）。終了姿勢では END_HOLD 秒保持してから記録停止
    （チャンク実行時に終了姿勢で「止まる」ことを教える）
  - アーム/グリッパ指令を毎フレーム 20Hz で publish（teleop_keyboard と同じ形式）
"""
import argparse
import math
import os
import random
import re
import signal
import subprocess
import sys
import time

import rclpy
from rclpy.node import Node
from builtin_interfaces.msg import Duration
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

# ---------------------------------------------------------------- 定数
ARM_TOPIC = "/crane_plus_arm_controller/joint_trajectory"
GRIP_TOPIC = "/crane_plus_gripper_controller/joint_trajectory"
ARM_JOINTS = ["crane_plus_joint1", "crane_plus_joint2",
              "crane_plus_joint3", "crane_plus_joint4"]
GRIP_JOINTS = ["crane_plus_joint_hand"]   # 要確認: ros2 topic echo /joint_states --once
RECORD_TOPICS = ["/joint_states", ARM_TOPIC, GRIP_TOPIC]

RATE = 20.0                 # Hz（teleop_keyboard と同じ）
HOME = [0.0, 0.0, 0.0, 0.0]
PITCH_SIGN = [1.0, -1.0, -1.0]   # joint2(肩)は正が前傾、joint3(肘)・joint4(手首)は負が前傾（10/18 単関節テストで確認）
GRIP_CLOSED = 0.0           # 要確認: Gazebo で開閉の向きを見て調整
GRIP_OPEN = 0.5

END_HOLD = 1.0             # 終了姿勢で保持する秒数（行動チャンクの後半に「止まる」を教える）
DEFAULT_ROOT = "/workspace/data/rosbags"   # /workspace はホストと共有（コンテナを消しても残る）


# ---------------------------------------------------------------- 軌道生成
def smooth(s: float) -> float:
    """0..1 -> 0..1 の補間（ease-out: すぐ動き出し、終点で減速）
    ※ 始点で速度0にすると、直立付近の行動≒0 となり state-only 方策が動き出せない"""
    return 1 - (1 - s) ** 2


def move(a, b, T, grip_a=GRIP_CLOSED, grip_b=None):
    """関節空間で a -> b へ T 秒で移動するフレーム列"""
    grip_b = grip_a if grip_b is None else grip_b
    n = max(2, int(T * RATE))
    frames = []
    for i in range(1, n + 1):
        s = smooth(i / n)
        arm = [x + (y - x) * s for x, y in zip(a, b)]
        frames.append((arm, grip_a + (grip_b - grip_a) * s))
    return frames


def jitter(rng, x, rel=0.05):
    return x * rng.uniform(1 - rel, 1 + rel)


def traj_bow(rng):
    """お辞儀（「お待たせしました」）: 前傾して止まる"""
    end = [0.0, jitter(rng, 0.35), jitter(rng, 0.7), jitter(rng, 0.35)]
    return move(HOME, end, jitter(rng, 1.6, 0.1))


def traj_point(rng, side):
    """指差し（「調味料はあちら」）: 根元を旋回して腕を伸ばす"""
    yaw = side * jitter(rng, 0.9)
    end = [yaw, jitter(rng, 0.5), jitter(rng, 0.9), jitter(rng, 0.1)]
    return move(HOME, end, jitter(rng, 2.0, 0.1))


def traj_serve(rng):
    """皿を差し出す: 上腕を少し前に、前腕を水平に伸ばしてグリッパを開く（テーブルに当たらない高さ）"""
    reach = [0.0, jitter(rng, 0.5), jitter(rng, 1.0), jitter(rng, 0.05)]
    f = move(HOME, reach, jitter(rng, 2.0, 0.1), GRIP_CLOSED)
    f += move(reach, reach, 0.6, GRIP_CLOSED, GRIP_OPEN)   # 開く
    return f


def traj_stir(rng):
    """鍋を混ぜる: 円を描きながら少しずつ持ち上げる（状態が反復しない）"""
    r1, r4 = jitter(rng, 0.25), jitter(rng, 0.2)
    base = [0.0, jitter(rng, 0.6), jitter(rng, 0.9), 0.6]
    loops, T_loop = 2, jitter(rng, 2.0, 0.1)
    start = [base[0], base[1], base[2], base[3] + r4]   # θ=0 の点
    f = move(HOME, start, 1.8)
    n = int(loops * T_loop * RATE)
    for i in range(1, n + 1):
        t = i / n
        th = 2 * math.pi * loops * t
        drift = -0.25 * t          # 肩を起こして持ち上げる（ドリフト）
        arm = [base[0] + r1 * math.sin(th),
               base[1] + drift,
               base[2],
               base[3] + r4 * math.cos(th)]
        f.append((arm, GRIP_CLOSED))
    return f


def traj_shake(rng):
    """塩を振る: 手首を小刻みに振りながら根元を横へ流す（状態が反復しない）"""
    amp, hz = jitter(rng, 0.25), jitter(rng, 2.5, 0.1)
    y0, y1 = jitter(rng, 0.3), -0.1
    pose = [y0, jitter(rng, 0.5), jitter(rng, 0.8), 0.5]
    f = move(HOME, pose, 1.6)
    T = 2.0
    n = int(T * RATE)
    for i in range(1, n + 1):
        t = i / n
        arm = [y0 + (y1 - y0) * t, pose[1], pose[2],
               pose[3] + amp * math.sin(2 * math.pi * hz * T * t)]
        f.append((arm, GRIP_CLOSED))
    return f


SKILLS = {
    "bow": traj_bow,
    "point_left": lambda rng: traj_point(rng, +1),
    "point_right": lambda rng: traj_point(rng, -1),
    "serve": traj_serve,
    "stir": traj_stir,
    "shake": traj_shake,
}


# ---------------------------------------------------------------- ROS ノード
class DemoRecorder(Node):
    def __init__(self):
        super().__init__("demo_recorder")
        self.arm_pub = self.create_publisher(JointTrajectory, ARM_TOPIC, 10)
        self.grip_pub = self.create_publisher(JointTrajectory, GRIP_TOPIC, 10)
        self.joints = {}
        self.create_subscription(JointState, "/joint_states", self._on_js, 10)

    def _on_js(self, msg):
        self.joints.update(dict(zip(msg.name, msg.position)))

    def _traj(self, names, pos, dt):
        msg = JointTrajectory()
        msg.joint_names = names
        p = JointTrajectoryPoint()
        p.positions = [float(x) for x in pos]
        sec = int(dt)
        p.time_from_start = Duration(sec=sec, nanosec=int((dt - sec) * 1e9))
        msg.points = [p]
        return msg

    def send(self, arm, grip, dt):
        self.arm_pub.publish(self._traj(ARM_JOINTS, [arm[0]] + [sg * x for sg, x in zip(PITCH_SIGN, arm[1:])], dt))
        self.grip_pub.publish(self._traj(GRIP_JOINTS, [grip], dt))

    def spin_for(self, sec):
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.01)

    def play(self, frames):
        period = 1.0 / RATE
        for arm, grip in frames:
            t0 = time.time()
            self.send(arm, grip, period * 2)
            self.spin_for(max(0.0, period - (time.time() - t0)))

    def current_arm(self):
        return [self.joints.get(j, 0.0) for j in ARM_JOINTS]

    def go_home(self, timeout=6.0):
        self.send(HOME, GRIP_CLOSED, 2.0)
        end = time.time() + timeout
        while time.time() < end:
            self.spin_for(0.2)
            if self.joints and max(abs(x) for x in self.current_arm()) < 0.03:
                self.spin_for(0.3)
                return True
        self.get_logger().warn("HOME に戻り切りませんでした（続行）")
        return False


# ---------------------------------------------------------------- rosbag
def next_episode_index(root):
    os.makedirs(root, exist_ok=True)
    idx = [int(m.group(1)) for d in os.listdir(root)
           if (m := re.fullmatch(r"ep(\d+)", d))]
    return max(idx) + 1 if idx else 0


def start_bag(path):
    proc = subprocess.Popen(["ros2", "bag", "record", "-o", path] + RECORD_TOPICS,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                            preexec_fn=os.setsid)
    return proc


def stop_bag(proc):
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)   # Ctrl+C と同じ
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", required=True, choices=SKILLS.keys())
    ap.add_argument("--mode", default="script", choices=["script", "teleop"])
    ap.add_argument("--episodes", type=int, default=15)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="記録せずに1回だけ動かす")
    args = ap.parse_args()

    rclpy.init()
    node = DemoRecorder()
    node.spin_for(1.0)   # /joint_states を受け取る
    rng = random.Random(args.seed)
    root = os.path.join(args.root, args.skill)

    try:
        if args.dry_run:
            node.go_home()
            frames = SKILLS[args.skill](rng)
            node.play(frames + [frames[-1]] * int(END_HOLD * RATE))
            node.spin_for(1.0)
            node.go_home()
            return

        if args.mode == "teleop":
            print("teleop モード: 各エピソードの前に teleop 側で X を押して直立に戻してください")
            print("※ teleop_keyboard 起動中はこのスクリプトから HOME 指令は出しません")

        start = next_episode_index(root)
        for k in range(args.episodes):
            ep = start + k
            path = os.path.join(root, f"ep{ep}")
            if args.mode == "script":
                node.go_home()
                bag = start_bag(path)
                node.spin_for(1.5)          # レコーダの購読開始待ち
                frames = SKILLS[args.skill](rng)
                node.play(frames + [frames[-1]] * int(END_HOLD * RATE))
                stop_bag(bag)
            else:
                cmd = input(f"[ep{ep}] Enter で記録開始（q で終了）: ")
                if cmd.strip().lower() == "q":
                    break
                bag = start_bag(path)
                input("  記録中… 動作が終わったらすぐ Enter: ")
                stop_bag(bag)
            print(f"  saved: {path}")
        node.go_home() if args.mode == "script" else None
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
