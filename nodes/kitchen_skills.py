"""kitchen_skills.py - 登録スキルの定義と計画の検証（plan_validator）

ROS にも OpenAI にも依存しないので、単体でテストできる:
    python3 kitchen_skills.py --selftest
"""
import json

# スキル名 → (日本語の意味, LLM に渡す説明)
SKILLS = {
    "bow":         ("お辞儀",           "お辞儀する。挨拶・「お待たせしました」・感謝・終わりの合図"),
    "point_left":  ("左を指す",         "ロボットから見て左を指差す。「あちら（左）にあります」の案内"),
    "point_right": ("右を指す",         "ロボットから見て右を指差す。「あちら（右）にあります」の案内"),
    "serve":       ("皿を差し出す",     "腕を前に伸ばしてグリッパを開く。料理を出す・渡す"),
    "stir":        ("鍋を混ぜる",       "円を描くように動かす。混ぜる・かき混ぜる・炒める"),
    "shake":       ("塩を振る",         "手首を小刻みに振る。塩・胡椒・調味料を振りかける"),
}
MAX_STEPS = 6
MAX_REPLY = 60


def skill_catalog_text():
    return "\n".join(f"- {k}: {v[1]}" for k, v in SKILLS.items())


def validate_plan(raw):
    """LLM の出力（str または dict）を検証する。
    戻り値: (ok: bool, plan: dict, errors: list[str])
    plan は正規化済み {steps:[{skill,say}], reply, rejected, reject_reason}
    """
    errors = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return False, None, [f"JSON として読めない: {e}"]
    if not isinstance(raw, dict):
        return False, None, ["最上位が JSON オブジェクトではない"]

    steps = raw.get("steps", [])
    rejected = bool(raw.get("rejected", False))
    reply = str(raw.get("reply", ""))[:MAX_REPLY]
    reason = str(raw.get("reject_reason", ""))

    if not isinstance(steps, list):
        errors.append("steps が配列ではない")
        steps = []
    norm = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            errors.append(f"steps[{i}] がオブジェクトではない")
            continue
        sk = st.get("skill")
        if sk not in SKILLS:
            errors.append(f"steps[{i}].skill='{sk}' は登録スキルにない（使えるのは {list(SKILLS)}）")
            continue
        norm.append({"skill": sk, "say": str(st.get("say", SKILLS[sk][0]))[:MAX_REPLY]})

    if rejected and norm:
        errors.append("rejected=true なのに steps が空ではない")
    if not rejected and not norm:
        errors.append("rejected=false なのに実行するステップがない")
    if len(norm) > MAX_STEPS:
        errors.append(f"ステップ数 {len(norm)} が上限 {MAX_STEPS} を超えている")
    if rejected and not reason:
        reason = "登録されたスキルでは実行できない指示"

    plan = {"steps": [] if rejected else norm[:MAX_STEPS], "reply": reply,
            "rejected": rejected, "reject_reason": reason}
    return (len(errors) == 0), plan, errors


def reject_plan(reason, reply="ごめんなさい、その指示は実行できません"):
    return {"steps": [], "reply": reply, "rejected": True, "reject_reason": reason}


def _selftest():
    cases = [
        ('{"steps":[{"skill":"stir","say":"混ぜます"},{"skill":"bow"}],"reply":"混ぜます","rejected":false}', True),
        ('{"steps":[],"reply":"できません","rejected":true,"reject_reason":"飛べない"}', True),
        ('{"steps":[{"skill":"fly"}],"reply":"","rejected":false}', False),
        ('{"steps":[{"skill":"bow"}],"reply":"","rejected":true}', False),
        ('not json', False),
        (json.dumps({"steps": [{"skill": "bow"}] * 7, "reply": "", "rejected": False}), False),
    ]
    ok_all = True
    for raw, expect in cases:
        ok, plan, errs = validate_plan(raw)
        mark = "OK " if ok == expect else "NG "
        ok_all &= (ok == expect)
        print(mark, ok, errs[:1])
    print("selftest:", "PASS" if ok_all else "FAIL")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(skill_catalog_text())
