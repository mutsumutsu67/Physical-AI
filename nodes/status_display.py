#!/usr/bin/env python3
"""status_display.py - 指示・LLM の計画・実行状況を表示するウィンドウ（動画用）

    cd /workspace/pai_kitchen/nodes && python3 status_display.py
Gazebo の横に並べて OBS で録画する。イベントは /workspace/data/events.jsonl に記録
（あとで make_srt.py で YouTube 字幕にする）。
"""
import json
import queue
import threading
import time
import tkinter as tk

import rclpy
from std_msgs.msg import String

from kitchen_skills import SKILLS

EVENT_LOG = "/workspace/data/events.jsonl"
FONT = "Noto Sans CJK JP"
BG, CARD, FG, SUB, ACC, OK, RUN = "#1F3A3D", "#EEF3F2", "#1C2526", "#5C6B6D", "#E07A2F", "#2A9D8F", "#E07A2F"


def log_event(kind, data):
    try:
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "kind": kind, "data": data}, ensure_ascii=False) + "\n")
    except OSError:
        pass


class Display:
    def __init__(self, root, q):
        self.root, self.q = root, q
        root.title("Kitchen Arm Status")
        root.configure(bg=BG)
        root.geometry("560x720")
        self.steps, self.states = [], []

        def section(title):
            fr = tk.Frame(root, bg=CARD, padx=16, pady=12)
            fr.pack(fill="x", padx=16, pady=(14, 0))
            tk.Label(fr, text=title, bg=CARD, fg=SUB, font=(FONT, 13, "bold"), anchor="w").pack(fill="x")
            return fr

        f1 = section("🎤 指示")
        self.instr = tk.Label(f1, text="（待機中）", bg=CARD, fg=FG, font=(FONT, 20, "bold"),
                              wraplength=500, justify="left", anchor="w")
        self.instr.pack(fill="x", pady=(6, 0))

        f2 = section("🧠 LLM の計画")
        self.plan_box = tk.Frame(f2, bg=CARD)
        self.plan_box.pack(fill="x", pady=(6, 0))

        f3 = section("💬 ロボット")
        self.say = tk.Label(f3, text="", bg=CARD, fg=ACC, font=(FONT, 22, "bold"),
                            wraplength=500, justify="left", anchor="w")
        self.say.pack(fill="x", pady=(6, 0))
        self.reply = tk.Label(f3, text="", bg=CARD, fg=SUB, font=(FONT, 14),
                              wraplength=500, justify="left", anchor="w")
        self.reply.pack(fill="x")

        self.footer = tk.Label(root, text="音声 × LLM × ACT ／ CRANE+ V2", bg=BG, fg="#8AB0AB", font=(FONT, 11))
        self.footer.pack(side="bottom", pady=10)
        root.after(50, self.poll)

    def render_plan(self):
        for w in self.plan_box.winfo_children():
            w.destroy()
        icons = {"wait": ("・", SUB), "running": ("▶", RUN), "ok": ("✅", OK), "timeout": ("⏱", ACC)}
        for i, (sk, st) in enumerate(zip(self.steps, self.states)):
            ic, col = icons.get(st, ("・", SUB))
            name = SKILLS.get(sk, (sk,))[0]
            weight = "bold" if st == "running" else "normal"
            tk.Label(self.plan_box, text=f"{ic}  {i + 1}. {name}（{sk}）", bg=CARD, fg=col if st != "wait" else FG,
                     font=(FONT, 17, weight), anchor="w").pack(fill="x")

    def poll(self):
        try:
            while True:
                topic, data = self.q.get_nowait()
                self.handle(topic, data)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def handle(self, topic, data):
        if topic == "/instruction":
            self.instr.config(text=f"「{data}」")
            self.steps, self.states = [], []
            self.render_plan()
            self.say.config(text="考え中…")
            self.reply.config(text="")
            log_event("instruction", data)
            return
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            return
        if topic == "/plan":
            if d.get("rejected"):
                self.steps, self.states = [], []
                self.render_plan()
                self.say.config(text="ごめんなさい、できません")
                self.reply.config(text=d.get("reject_reason", ""))
            else:
                self.steps = [s["skill"] for s in d.get("steps", [])]
                self.states = ["wait"] * len(self.steps)
                self.render_plan()
                self.say.config(text="")
                self.reply.config(text=d.get("reply", ""))
            log_event("plan", d)
        elif topic == "/skill_status":
            i, st = d.get("index", -1), d.get("state")
            if st == "running" and 0 <= i < len(self.states):
                self.states[i] = "running"
                self.say.config(text=d.get("say", ""))
            elif st == "finished" and 0 <= i < len(self.states):
                self.states[i] = "ok" if d.get("ok") else "timeout"
            elif st == "done":
                self.say.config(text="完了しました")
            self.render_plan()
            log_event("status", d)


def main():
    q = queue.Queue()
    rclpy.init()
    node = rclpy.create_node("status_display")
    for t in ("/instruction", "/plan", "/skill_status"):
        node.create_subscription(String, t, lambda m, t=t: q.put((t, m.data)), 10)
    th = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    th.start()
    root = tk.Tk()
    Display(root, q)
    try:
        root.mainloop()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
