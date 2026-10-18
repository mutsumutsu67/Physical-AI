#!/usr/bin/env python3
"""instruction_bridge.py - 共有フォルダ /workspace/inbox に置かれたテキストを /instruction に publish

    cd /workspace/pai_kitchen/nodes && python3 instruction_bridge.py
Windows 側の voice_input.py が docker cp で inbox/*.txt を置く → 読んで publish → inbox/done/ へ移動
"""
import os
import shutil
import time

import rclpy
from std_msgs.msg import String

INBOX = "/workspace/inbox"
DONE = os.path.join(INBOX, "done")


def main():
    os.makedirs(DONE, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node("instruction_bridge")
    pub = node.create_publisher(String, "/instruction", 10)
    node.get_logger().info(f"watching {INBOX}/*.txt → /instruction")
    try:
        while rclpy.ok():
            files = sorted(f for f in os.listdir(INBOX) if f.endswith(".txt"))
            for name in files:
                path = os.path.join(INBOX, name)
                time.sleep(0.1)                      # 書き込み完了待ち
                try:
                    text = open(path, encoding="utf-8").read().strip()
                except OSError:
                    continue
                if text:
                    pub.publish(String(data=text))
                    node.get_logger().info(f"🎤 {text}")
                shutil.move(path, os.path.join(DONE, name))
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
