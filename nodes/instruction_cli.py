#!/usr/bin/env python3
"""instruction_cli.py - キーボードで打った指示を /instruction に publish（テキスト入力版）
    python3 instruction_cli.py
"""
import rclpy
from std_msgs.msg import String


def main():
    rclpy.init()
    node = rclpy.create_node("instruction_cli")
    pub = node.create_publisher(String, "/instruction", 10)
    print("指示を入力して Enter（q で終了）")
    try:
        while True:
            text = input("> ").strip()
            if text.lower() == "q":
                break
            if text:
                pub.publish(String(data=text))
                rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, EOFError):
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
