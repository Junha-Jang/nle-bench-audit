#!/usr/bin/env python3
"""거짓 성공 탐지 — 정답을 훼손한 변종이 통과하는가 (2026-08-14).

성공 0건 감사는 **실패하는 쪽**만 본다.
거짓 성공은 **통과하는 쪽**에 숨어 있으므로 반대편에서 들어가야 한다.

★ 방법: **정답에 대한 변이 검정.** 이제 검증된 정답이 633개 있다.
정답을 고의로 훼손한 변종을 만들어 채점한다. **변종이 여전히 통과하면
제약 집합이 지시가 요구하는 것을 붙잡지 못하고 있다는 뜻이다.**

변이 넷:
  M1 **단계 누락** — 정답의 i번째 단계를 빼고 나머지를 실행. 각 단계가 필요한가?
  M2 **삭제 대체** — 대상 엔티티를 편집하는 대신 삭제. (실제로 이 형태의 허위 통과가 있었다:
       `attribute_changed` 는 엔티티가 사라지면 True 를 돌려준다.)
  M3 **대상 오조준** — 같은 종류의 다른 엔티티에 같은 편집을 적용.
  M4 **값 왜곡** — 수치 파라미터를 요구와 다른 값으로.

통과하면 안 되는 변종이 통과한 경우만 보고한다.
새 모델 실행 0회 · GPU 0시간.
"""
from __future__ import annotations

import copy
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "<DEV_CHECKOUT>/src")
import yaml                                                        # noqa: E402
from nlebench.dataset.fixtures import build_fixture                # noqa: E402
from nlebench.runner.constraints import validate_named_constraints  # noqa: E402
from nlebench.runner.scenario_loader import load_scenario          # noqa: E402
from nlebench.tools.executor import execute_tool                   # noqa: E402

SC = "<DEV_CHECKOUT>/src/nlebench/dataset/scenarios_v3_1/feasible"

# 편집 대상을 지목하는 파라미터 이름 → 삭제 도구
TARGET_KEYS = {"clip_id": "remove_clip", "caption_id": "remove_caption",
               "effect_id": "remove_effect", "transition_id": "remove_transition",
               "track_id": "remove_track", "media_id": "remove_media"}
SKIP_NUMERIC = {"index"}


def run(doc, scenario, steps):
    """수순을 실행하고 (모든 호출 성공?, TSR) 을 돌려준다."""
    initial = build_fixture(doc["fixture"])
    final = build_fixture(doc["fixture"])
    for step in steps:
        try:
            result = execute_tool(final, step["tool"], copy.deepcopy(step["params"]))
        except Exception:
            return False, False
        if not result.get("success"):
            return False, False
    try:
        passed, _ = validate_named_constraints(initial, final, scenario)
    except Exception:
        return True, False
    return True, passed


def main():
    findings = defaultdict(list)
    stats = Counter()
    for path in sorted(glob.glob(f"{SC}/**/*.yaml", recursive=True)):
        doc = yaml.safe_load(open(path)) or {}
        gold = doc.get("reference_solution")
        if not gold:
            stats["정답없음"] += 1
            continue
        sid = doc["id"]
        scenario = load_scenario(Path(path))
        ok, passed = run(doc, scenario, gold)
        if not (ok and passed):
            stats["정답이 통과 안 함(별도 문제)"] += 1
            continue
        stats["검사한 시나리오"] += 1

        # ── M1 단계 누락 ──────────────────────────────────────────
        if len(gold) > 1:
            for i in range(len(gold)):
                mutant = [s for j, s in enumerate(gold) if j != i]
                ok_m, passed_m = run(doc, scenario, mutant)
                stats["M1 시행"] += 1
                if ok_m and passed_m:
                    findings["M1_단계가_불필요"].append(
                        {"scenario": sid, "dropped_step": i,
                         "tool": gold[i]["tool"], "params": gold[i]["params"]})

        # ── M2 삭제 대체 ─────────────────────────────────────────
        # 과제 자체가 삭제인 시나리오는 제외한다 — 삭제로 통과하는 것이 정답이므로
        # 이것을 세면 항등식을 결함으로 세는 셈이다.
        gold_removes = {s["params"].get(k) for s in gold for k in TARGET_KEYS
                        if s["tool"].startswith("remove_") and s["params"].get(k)}
        for step in gold:
            for key, remover in TARGET_KEYS.items():
                target = step["params"].get(key)
                if not isinstance(target, str) or target.startswith("$"):
                    continue
                if target in gold_removes:
                    continue
                mutant = [{"tool": remover, "params": {key: target}}]
                ok_m, passed_m = run(doc, scenario, mutant)
                stats["M2 시행"] += 1
                if ok_m and passed_m:
                    findings["M2_삭제만으로_통과"].append(
                        {"scenario": sid, "removed": target, "tool": remover})
                break

        # ── M3 대상 오조준 ───────────────────────────────────────
        state = build_fixture(doc["fixture"])
        clip_ids = [c.id for t in state.timelines for tr in t.tracks for c in tr.clips]
        for si, step in enumerate(gold):
            target = step["params"].get("clip_id")
            if not isinstance(target, str) or target.startswith("$"):
                continue
            others = [c for c in clip_ids if c != target]
            if not others:
                continue
            mutant = copy.deepcopy(list(gold))
            mutant[si]["params"]["clip_id"] = others[0]
            ok_m, passed_m = run(doc, scenario, mutant)
            stats["M3 시행"] += 1
            if ok_m and passed_m:
                findings["M3_다른_대상에_해도_통과"].append(
                    {"scenario": sid, "step": si, "intended": target, "used": others[0]})
            break

        # ── M4 값 왜곡 ───────────────────────────────────────────
        for si, step in enumerate(gold):
            done = False
            for key, val in step["params"].items():
                if key in SKIP_NUMERIC or isinstance(val, bool):
                    continue
                if not isinstance(val, (int, float)):
                    continue
                mutant = copy.deepcopy(list(gold))
                mutant[si]["params"][key] = float(val) + 7.0
                ok_m, passed_m = run(doc, scenario, mutant)
                stats["M4 시행"] += 1
                if ok_m and passed_m:
                    findings["M4_값이_틀려도_통과"].append(
                        {"scenario": sid, "step": si, "param": key,
                         "intended": val, "used": float(val) + 7.0})
                done = True
                break
            if done:
                break

    out = {"_meta": {
        "what": "검증된 정답을 훼손한 변종이 통과하는지 본다 — 거짓 성공 탐지",
        "why": ("성공 0건 감사는 거짓 실패만 본다. 거짓 성공은 통과하는 쪽에 숨으므로 "
                "반대편에서 들어가야 한다."),
        "mutations": {"M1": "정답의 한 단계를 뺀다", "M2": "편집 대신 대상을 삭제한다",
                      "M3": "같은 편집을 다른 대상에 한다", "M4": "수치를 7 만큼 틀리게 준다"},
        "scoring_path": "validate_named_constraints (하네스 경로)",
        "note": "변종이 통과하면 제약 집합이 지시를 못 붙잡는 것이다."},
        "stats": dict(stats),
        "findings": {k: v for k, v in findings.items()},
        "counts": {k: len(v) for k, v in findings.items()}}
    json.dump(out, open("<LEDGER_DIR>/false_success_probe.json", "w"),
              indent=1, ensure_ascii=False)
    print(json.dumps(dict(stats), ensure_ascii=False))
    print("\n발견:", json.dumps(out["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
