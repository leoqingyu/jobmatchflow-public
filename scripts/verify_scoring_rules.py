"""
一次性验证脚本：不碰 DB/LLM，直接对 ai/scoring_rules.compute_final_score 灌造造数据，
检查类目权重乘数 + 资历错配封顶这两块新逻辑的算术对不对。

跑法：python3 scripts/verify_scoring_rules.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.scoring_rules import apply_preference_bonus, compute_final_score


def _req(rid, category, importance="must", hard_constraint=None):
    return {"id": rid, "category": category, "importance": importance, "hard_constraint": hard_constraint}


def _match(rid, level):
    return {"requirement_id": rid, "match_level": level, "evidence_ids": [], "reason": "", "confidence": 1.0}


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    return cond


def main():
    ok = True

    # 1. 语言权重(x1.5)比技能权重(x0.6)更"狠"：固定一条命中的 skill/full 打底，
    # 第二条 must/none 分别设成 skill 和 language，权重更高的 language 应该把总分拖得更低。
    fixed_hit = _req("r0", "skill")
    fixed_match = _match("r0", "full")
    skill_fail = compute_final_score(
        [fixed_hit, _req("r1", "skill")], [fixed_match, _match("r1", "none")]
    )
    lang_fail = compute_final_score(
        [fixed_hit, _req("r1", "language")], [fixed_match, _match("r1", "none")]
    )
    ok &= check(
        f"language-fail score={lang_fail['score']} < skill-fail score={skill_fail['score']}",
        lang_fail["score"] < skill_fail["score"],
    )

    # 2. 硬性条件封顶(70) + 资历封顶(50) 同时命中，取更狠的 min(70, 50) = 50
    # 用 20 条命中的 skill 撑住原始加权分（避免它本来就低于两个封顶值，测不出封顶效果）
    reqs = [_req(f"s{i}", "skill") for i in range(20)] + [
        _req("r1", "language", hard_constraint="language")
    ]
    matches = [_match(f"s{i}", "full") for i in range(20)] + [_match("r1", "none")]
    uncapped = compute_final_score(reqs, matches)
    ok &= check(
        f"raw weighted score before seniority cap = {uncapped['score']} (sanity: between 50 and 70)",
        50 < uncapped["score"] <= 70,
    )
    both_capped = compute_final_score(
        reqs, matches, job_seniority="senior", candidate_total_years=1.0
    )
    ok &= check(
        f"both caps -> score={both_capped['score']} == 50",
        both_capped["score"] == 50.0,
    )
    ok &= check(
        "both caps -> cap_applied and seniority_mismatch both True",
        both_capped["score_breakdown"]["cap_applied"] and both_capped["score_breakdown"]["seniority_mismatch"],
    )

    # 3. job_seniority="mid" 永不触发资历封顶，不管候选人年限多离谱
    mid_reqs = [_req("r1", "skill")]
    mid_matches = [_match("r1", "full")]
    mid_low = compute_final_score(mid_reqs, mid_matches, job_seniority="mid", candidate_total_years=0.5)
    mid_high = compute_final_score(mid_reqs, mid_matches, job_seniority="mid", candidate_total_years=20.0)
    ok &= check(
        "job_seniority=mid never triggers seniority cap (low years)",
        not mid_low["score_breakdown"]["seniority_mismatch"],
    )
    ok &= check(
        "job_seniority=mid never triggers seniority cap (high years)",
        not mid_high["score_breakdown"]["seniority_mismatch"],
    )

    # 4. candidate_total_years=None 时，不管 job_seniority 是什么都不触发
    none_years = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="senior", candidate_total_years=None
    )
    ok &= check(
        "candidate_total_years=None never triggers seniority cap",
        not none_years["score_breakdown"]["seniority_mismatch"],
    )

    # 5. senior 岗位：候选人刚好 3 年（边界，含）不该触发；差一点点（2.9 年）该触发硬封顶，
    #    且没有对称上限——经验再多对 senior 岗位也不算 mismatch。
    senior_boundary_ok = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="senior", candidate_total_years=3.0
    )
    ok &= check(
        "senior job + 3y candidate (boundary, inclusive) does not trigger mismatch",
        not senior_boundary_ok["score_breakdown"]["seniority_mismatch"],
    )
    senior_hard = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="senior", candidate_total_years=2.9
    )
    ok &= check(
        f"senior job + 2.9y candidate hard-capped at 50 (score={senior_hard['score']})",
        senior_hard["score_breakdown"]["seniority_mismatch_cap"] == 50 and senior_hard["score"] == 50.0,
    )
    senior_no_upper_bound = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="senior", candidate_total_years=50.0
    )
    ok &= check(
        "senior job + 50y candidate does not trigger mismatch (no upper bound)",
        not senior_no_upper_bound["score_breakdown"]["seniority_mismatch"],
    )

    # 6. junior 岗位三档：<=3 年完全没问题，4 年软封顶 70，>=5 年硬封顶 50
    junior_fine = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="junior", candidate_total_years=3.0
    )
    ok &= check(
        "junior job + 3y candidate does not trigger mismatch",
        not junior_fine["score_breakdown"]["seniority_mismatch"],
    )
    junior_soft = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="junior", candidate_total_years=4.0
    )
    ok &= check(
        f"junior job + 4y candidate soft-capped at 70 (score={junior_soft['score']})",
        junior_soft["score_breakdown"]["seniority_mismatch_cap"] == 70 and junior_soft["score"] == 70.0,
    )
    junior_mismatch = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")], job_seniority="junior", candidate_total_years=10.0
    )
    ok &= check(
        "junior job + 10y candidate triggers hard mismatch",
        junior_mismatch["score_breakdown"]["seniority_mismatch"]
        and junior_mismatch["score_breakdown"]["seniority_mismatch_cap"] == 50,
    )

    # 7. 附加分：普通岗位（无封顶）加成正常叠加，且不超过 100
    plain = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "full")]
    )  # score ~90 (full=0.9 * 100)
    plain_bonus = apply_preference_bonus(plain, 10, "great fit")
    ok &= check(
        f"plain job + bonus 10 -> score={plain_bonus['score']} == min(100, {plain['score']}+10)",
        plain_bonus["score"] == min(100.0, plain["score"] + 10),
    )
    over_100 = compute_final_score(
        [_req("r1", "skill")], [_match("r1", "exceeds")]
    )  # score == 100
    over_100_bonus = apply_preference_bonus(over_100, 10, "great fit")
    ok &= check(
        f"score already 100 + bonus 10 -> still 100 (score={over_100_bonus['score']})",
        over_100_bonus["score"] == 100.0,
    )

    # 8. 附加分不能突破硬性条件封顶(70)
    hard_capped = compute_final_score(reqs, matches)  # 20 skill full + 1 language hard_constraint none -> capped 70
    ok &= check("sanity: hard_capped is actually capped at 70", hard_capped["score"] == 70.0)
    hard_capped_bonus = apply_preference_bonus(hard_capped, 10, "great fit")
    ok &= check(
        f"hard-constraint-capped job + bonus 10 -> still capped at 70 (score={hard_capped_bonus['score']})",
        hard_capped_bonus["score"] == 70.0,
    )

    # 9. 附加分不能突破资历错配封顶(50)
    seniority_capped_bonus = apply_preference_bonus(both_capped, 10, "great fit")
    ok &= check(
        f"seniority-mismatch-capped job (score=50) + bonus 10 -> still 50 (score={seniority_capped_bonus['score']})",
        seniority_capped_bonus["score"] == 50.0,
    )

    # 10. bonus 输入越界/非法时 clamp 到 [0, 10]
    clamp_high = apply_preference_bonus(plain, 999, None)
    clamp_negative = apply_preference_bonus(plain, -5, None)
    ok &= check(
        f"bonus=999 clamps to 10 (score_breakdown.preference_bonus={clamp_high['score_breakdown']['preference_bonus']})",
        clamp_high["score_breakdown"]["preference_bonus"] == 10,
    )
    ok &= check(
        f"bonus=-5 clamps to 0 (score_breakdown.preference_bonus={clamp_negative['score_breakdown']['preference_bonus']})",
        clamp_negative["score_breakdown"]["preference_bonus"] == 0,
    )

    print("\nALL PASS" if ok else "\nSOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
