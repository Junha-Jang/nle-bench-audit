#!/usr/bin/env python3
"""거짓 성공 탐지 — **확장판** (2026-08-14).

false_success_probe.py 는 하한이었다. M4(값 왜곡)가 **시나리오당 첫 수치 파라미터
하나**만 변이하고 멈추기 때문이다(원본 138-157행: 내부 루프도 `break`, 외부 루프도
`break`). 그래서 "정답 두 번째 단계의 caption start" 처럼 뒤에 있는 값은
한 번도 검사되지 않았다.

확장 둘:

  **M4-full** 정답의 **모든 단계 × 모든 수치 파라미터**를 각각 +7 로 왜곡한다.
       (원본은 시나리오당 1회 시행 → 확장은 시나리오당 평균 여러 회)

  **M5 문자열 왜곡**(신규) 자막 텍스트·이름·파일 경로처럼 지시가 **글자 그대로**
       못 박는 리터럴을 다른 값으로 바꾼다. 트랜지션/이펙트 종류는 유효한
       **다른** 종류로 바꾼다. 엔티티 id 로 쓰이는 키는 건드리지 않는다
       (그것은 M3 대상 오조준이 다루는 축이다).

원본과 같은 하네스 경로로만 채점한다:
  validate_named_constraints + load_scenario + build_fixture + execute_tool.

★ 원본에 없던 안전장치: 픽스처 스펙을 deepcopy 해서 넘긴다. apply_patch 가 스펙
   dict 를 건드리면 같은 스펙을 공유하는 다음 실행이 오염돼 허위 발견이 생긴다
   (앞 라운드에서 M1 4건이 이 아티팩트로 판명됐다).

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
OUT = "<DEV_CHECKOUT>/<LEDGER_DIR>/false_success_probe_full.json"

SKIP_NUMERIC = {"index"}

# 엔티티를 지목하는 키 — 문자열이지만 리터럴이 아니다. M5 대상 아님.
ID_KEYS = {
    "clip_id", "clip_id_1", "clip_id_2", "track_id", "media_id", "effect_id",
    "transition_id", "caption_id", "link_id", "sequence_id", "timeline_id",
    "bin_id", "video_clip_id", "audio_clip_id", "media_ids", "bin_ids",
    "entity", "action", "type", "alignment", "easing",
}
# 지시가 글자 그대로 못 박는 리터럴
TEXT_KEYS = {"text", "name", "file_path", "label", "title"}
TRANSITIONS = ["cross_dissolve", "dip_to_black", "dip_to_white", "wipe", "slide", "constant_power"]
EFFECTS = ["blur", "brightness", "contrast", "saturation", "crop", "fade_in", "fade_out"]


def run(doc, scenario, steps):
    """수순을 실행하고 (모든 호출 성공?, TSR) 을 돌려준다."""
    spec = copy.deepcopy(doc["fixture"])
    initial = build_fixture(copy.deepcopy(spec))
    final = build_fixture(copy.deepcopy(spec))
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


def string_mutations(key, val):
    """이 파라미터에 줄 '틀린 값' 을 돌려준다(없으면 빈 리스트)."""
    if key in ID_KEYS or not isinstance(val, str):
        return []
    if key == "transition_type":
        return [t for t in TRANSITIONS if t != val][:1]
    if key == "effect_type":
        return [e for e in EFFECTS if e != val][:1]
    if key in TEXT_KEYS:
        return ["MUTATED_" + val]
    return []


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

        # ── M4-full 모든 단계 × 모든 수치 파라미터 ────────────────
        for si, step in enumerate(gold):
            for key, val in step["params"].items():
                if key in SKIP_NUMERIC or isinstance(val, bool):
                    continue
                if not isinstance(val, (int, float)):
                    continue
                mutant = copy.deepcopy(list(gold))
                mutant[si]["params"][key] = float(val) + 7.0
                ok_m, passed_m = run(doc, scenario, mutant)
                stats["M4-full 시행"] += 1
                if ok_m and passed_m:
                    findings["M4full_값이_틀려도_통과"].append(
                        {"scenario": sid, "step": si, "tool": step["tool"], "param": key,
                         "intended": val, "used": float(val) + 7.0})

        # ── M5 문자열 리터럴 왜곡 ────────────────────────────────
        for si, step in enumerate(gold):
            for key, val in step["params"].items():
                for wrong in string_mutations(key, val):
                    mutant = copy.deepcopy(list(gold))
                    mutant[si]["params"][key] = wrong
                    ok_m, passed_m = run(doc, scenario, mutant)
                    stats["M5 시행"] += 1
                    if ok_m and passed_m:
                        findings["M5_문자열이_틀려도_통과"].append(
                            {"scenario": sid, "step": si, "tool": step["tool"], "param": key,
                             "intended": val, "used": wrong})

    out = {"_meta": {
        "what": "false_success_probe.py 의 확장 — 모든 수치 파라미터(M4-full) + 문자열 리터럴(M5)",
        "why": ("원본 M4 는 시나리오당 첫 수치 파라미터 하나만 보고 break 했다. "
                "따라서 79건(→보강 후 26건)은 하한이었다. 뒤 단계의 값과 "
                "자막 텍스트·이름 같은 문자열 리터럴은 한 번도 검사되지 않았다."),
        "mutations": {
            "M4-full": "정답의 모든 단계 × 모든 수치 파라미터를 각각 +7",
            "M5": "자막 텍스트/이름/경로는 다른 문자열로, 트랜지션·이펙트 종류는 유효한 다른 종류로"},
        "scoring_path": "validate_named_constraints (하네스 경로)",
        "차이점": "픽스처 스펙을 deepcopy 해서 넘긴다(앞 라운드 P6 오탐 방지)",
        "note": "변종이 통과하면 제약 집합이 지시를 못 붙잡는 것이다."},
        "stats": dict(stats),
        "findings": {k: v for k, v in findings.items()},
        "counts": {k: len(v) for k, v in findings.items()}}
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
    print(json.dumps(dict(stats), ensure_ascii=False))
    print("\n발견:", json.dumps(out["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
