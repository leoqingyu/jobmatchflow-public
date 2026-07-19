"""
简历生成编排：读经历库+facts -> 选材（ai/resume_selection.py）-> 写偏好事件 ->
改写（ai/resume_rewrite.py）-> 组装 content_json（固定基本信息快照 + 选中经历 + 技能）->
持久化 GeneratedAsset(asset_type=RESUME_JSON，跟求职信一样单行存 JSON+文件路径，不新开
RESUME_DOCX 行）-> 渲染 DOCX（renderer/docx_render.py）-> 存文件、更新 file_path。

一页强制，不靠"渲染完数页数、超了再反复调整"那种循环——经历数最多就 3-4 条，能用的固定
形状只有两种，选完直接改写+渲染一次就定型：
- 选中 4 条经历：形状是 3-3-2-2 还是 3-3-3，由 ai.resume_rewrite.decide_keep_fourth_experience
  判断一次——看第四条经历对这个 JD 是否有独特、值得保留的价值，值得就留全部 4 条（第三、四条
  各 2 个 bullet），不值得就砍掉第四条、第三条写满 3 个。两种形状按当前字号/页边距的经验值
  预算都稳妥落在一页内，不需要再验证。
- 选中 3 条经历（经历库本来就不够 4 条，或形状判断砍掉了第四条）：3-3-3，全部写满。
- Skills 区块每个分类（含 Languages 那一行）在 _fit_line_to_one_line 里被收在一行内，跟
  是否超页无关——类目本来就该是一行，不是等超页了才管；超出预算时从列表末尾开始删，真实
  技能（tier 1，用于过 ATS 关键词匹配）排在最前，删的是末尾的 LLM 补充候选词或最不重要的
  技能，真实技能优先保留。
- 恰好一页但经历库还有没选进来的候选（选材阶段按 MAX_EXPERIENCE_ITEMS 砍掉的那些，包括
  形状判断主动砍掉的第四条）：贪心按优先级从高到低往回加，每加一条就重新改写+渲染+数页，
  加了还是一页就保留、试下一条；加了变成两页就撤销这一条、到此为止——保证不是"随便凑够
  一页就收工"，而是在不超页的前提下尽量塞满。这个阶段每加一条都要多一次改写 LLM 调用
  （只对新加的这一条经历，不是重新写全部），但只有真的一页有富余空间时才会触发。
"""

from __future__ import annotations

import base64
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.constants import AssetStatus, AssetType, PreferenceAction
from core.llm_usage_tracking import llm_usage_context
from core.logger import get_logger
from ai.llm_factory import get_generation_llm_client
from ai.resume_rewrite import (
    FULL_BULLET_TOP_N,
    MAX_BULLETS_PER_EXPERIENCE,
    MIN_BULLETS_PER_EXPERIENCE,
    categorize_skills,
    decide_keep_fourth_experience,
    polish_bullets,
    rewrite_experience_bullets,
)
from ai.resume_selection import MAX_EXPERIENCE_ITEMS, order_for_display, select_experience_units, select_skills
from db.models import (
    GeneratedAsset,
    Job,
    UserCandidateFacts,
    UserExperienceUnit,
    UserJobScore,
    UserProfile,
)
from renderer.docx_render import (
    MAX_BULLET_CHARS,
    MAX_SKILLS,
    count_pages,
    render_preview_png,
    render_tailored_resume_docx,
)
from services.preference_service import compute_preference_adjustments, log_preference_event
from services.profile_photo import profile_photo_path
from services.profile_service import ProfileService
from services.resume_storage import remove_file_if_exists, write_resume_docx

logger = get_logger(__name__)

# Skills 每个分类行（含 Languages）目标"读起来是一整行"——按当前页边距（左右各 0.6in，
# Letter 页宽 8.5in）和正文字号（11pt 左右）粗算，单栏纯文本大约能排下 90-95 字符一行，
# 这是经验值，不是精确排版测量。超过就从列表末尾开始删，真实技能（tier 1）排在列表最前、
# LLM 补充候选词排在最后，删末尾天然优先保真实技能，见 ai/prompts/resume_skills_categorize_v1.txt。
_SKILL_LINE_MAX_CHARS = 95


def _fit_line_to_one_line(label: str, items: list[str], max_chars: int) -> list[str]:
    trimmed = list(items)
    while len(trimmed) > 1 and len(f"{label}: {', '.join(trimmed)}") > max_chars:
        trimmed.pop()
    return trimmed


def _photo_path_for(user_id: int) -> str | None:
    path = profile_photo_path(user_id)
    return str(path) if path.is_file() else None


def is_tailored_resume_json(j: dict | None) -> bool:
    """定制简历 JSON（结构化 experience/education/skills 等）的粗校验，非空/非残缺内容。"""
    if not j:
        return False
    return "full_name" in j and ("experience" in j or "education" in j)


def update_resume_content(db: Session, asset_id: int, user_id: int, payload: dict) -> str:
    """
    用户手动编辑简历 content_json 后，直接按编辑结果重新渲染 DOCX 并覆盖旧文件——不重新选材/
    改写，编辑内容就是最终内容。跟 ResumeGenerationService.apply_selection_adjustment 不同：
    那个是"调整选中哪些经历"（走选材+改写），这个是"文字本身改了"（直接渲染，不碰 LLM）。
    返回渲染后的文件绝对路径。
    """
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    if asset.asset_type != AssetType.RESUME_JSON.value:
        raise ValueError("仅支持 resume_json 类型")

    merged = {**(asset.content_json or {}), **payload}
    if "skills" in payload and "skill_categories" not in payload:
        # 手动改了扁平技能列表、但没有一并给出分类（比如走的是旧版单行编辑），之前 LLM
        # 分类的结果就跟不上了——不重新调 LLM（这个函数本来就是"编辑内容直接渲染，不碰
        # LLM"的路径），退回渲染层的单行 Skills 兜底，等下次真正走生成/调整入口时才会
        # 重新分类。如果 payload 里连 skill_categories 一起给了（Materials Editor 现在
        # 就是直接编辑分类分组），那说明用户编辑的就是分类本身，直接信它，不失效。
        merged.pop("skill_categories", None)
    docx_bytes = render_tailored_resume_docx(merged, photo_path=_photo_path_for(user_id))
    path = write_resume_docx(docx_bytes, user_id, asset.job_id, asset.file_path)
    asset.content_json = merged
    asset.file_path = path
    db.flush()
    return path


def render_resume_preview(db: Session, asset_id: int, user_id: int, draft_payload: dict) -> dict:
    """
    编辑页面实时预览：不需要先保存——把草稿内容合并到已存的 content_json 上（跟
    update_resume_content 同一个合并方式，但不落库、不写文件），渲染成 DOCX 再转一张低
    分辨率缩略图，供 Materials Editor 旁边实时展示"改完大概长什么样"，不用每次编辑都下载
    DOCX 才能看排版效果。返回 {"pages": int|None, "thumbnail_png_base64": str|None}——
    LibreOffice/PyMuPDF 不可用时两者都是 None，前端按"预览暂不可用"处理，不阻断编辑。
    """
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    if asset.asset_type != AssetType.RESUME_JSON.value:
        raise ValueError("仅支持 resume_json 类型")

    merged = {**(asset.content_json or {}), **draft_payload}
    docx_bytes = render_tailored_resume_docx(merged, photo_path=_photo_path_for(user_id))
    pages, png_bytes = render_preview_png(docx_bytes)
    return {
        "pages": pages,
        "thumbnail_png_base64": base64.b64encode(png_bytes).decode("ascii") if png_bytes else None,
    }


class ResumeGenerationService:
    def __init__(self, db: Session, user_id: int | None = None):
        """
        user_id 给定时，按该用户在 Settings 里选的 generation_model
        （UserProfile.generation_model，见 core.constants.GenerationModel）在 Gemini/Claude
        之间选实际调用的 LLM；不给（比如只做只读查询、不确定 user_id 的老调用路径）就退回
        Gemini 默认——不强制每个调用方都先查一遍 profile。同理查一遍
        resume_tailoring_mode（core.constants.ResumeTailoringMode），缺省 "honest"，只影响
        bullet 改写用哪份 prompt（见 ai/resume_rewrite.py），"honest" 这条路径行为完全不变。
        """
        self.db = db
        profile_service = ProfileService(db)
        model_choice = profile_service.get_generation_model(user_id) if user_id is not None else None
        self.llm = get_generation_llm_client(model_choice)
        self.tailoring_mode = (
            (profile_service.get_resume_tailoring_mode(user_id) if user_id is not None else None) or "honest"
        )

    # ------------------------------------------------------------------
    # 主入口：全新生成
    # ------------------------------------------------------------------
    def generate_for_job(self, user_id: int, job_id: int) -> GeneratedAsset:
        with llm_usage_context(user_id):
            return self._generate_for_job_impl(user_id, job_id)

    def _generate_for_job_impl(self, user_id: int, job_id: int) -> GeneratedAsset:
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")
        score = self._get_score(user_id, job_id)
        if not score:
            raise ValueError("该岗位尚无打分记录，无法生成定制简历")

        units = self._load_units(user_id)
        if not units:
            raise ValueError("经历库为空，无法生成定制简历——请先录入经历")
        atoms = self._load_atoms(user_id)

        requirement_matches = score.requirement_matches or []
        must_have_requirements = self._must_have_requirements(job)
        nice_to_have_requirements = self._nice_to_have_requirements(job)
        job_context = job.domain or "unknown"
        preference_adjustments = compute_preference_adjustments(self.db, user_id, job_context)
        basic_info = self._basic_info_snapshot(user_id)

        selected_raw = select_experience_units(
            units, requirement_matches, preference_adjustments, MAX_EXPERIENCE_ITEMS
        )
        if not selected_raw:
            raise ValueError("没有可选经历，无法生成简历")

        # 选中 4 条经历时，形状只有两种，交给 LLM 判断一次就定下来（见
        # ai.resume_rewrite.decide_keep_fourth_experience 的说明），不走"渲染完数页数、
        # 超了再反复调整"那一套：
        # - 3-3-2-2：留全部 4 条，第三、四条各写 2 条 bullet；
        # - 3-3-3：砍掉优先级最低的第四条，第三条写满 3 条。
        dropped_fourth_id: int | None = None
        if len(selected_raw) == MAX_EXPERIENCE_ITEMS:
            keep_fourth = decide_keep_fourth_experience(
                self.llm, selected_raw[2], selected_raw[3], must_have_requirements,
                nice_to_have_requirements=nice_to_have_requirements,
                job_title=job.title, job_domain=job.domain,
            )
            if not keep_fourth:
                dropped_fourth_id = selected_raw[3]["id"]
                selected_raw = selected_raw[:3]
                logger.info(
                    "简历经历形状=3-3-3：砍掉优先级最低的第四条 unit_id=%s，第三条写满",
                    dropped_fourth_id,
                )

        # 一页有富余空间时可以往回加的候选池：选材阶段被 MAX_EXPERIENCE_ITEMS 砍掉的那些，
        # 按各自 priority 从高到低排（跟 selected_raw 用同一套 priority 打分，可比）。刚被
        # 形状判断主动砍掉的第四条也排除在外——那是权衡过价值后的决定，不该被回填逻辑
        # 悄悄加回来。
        selected_ids = {u["id"] for u in selected_raw}
        if dropped_fourth_id is not None:
            selected_ids.add(dropped_fourth_id)
        full_sorted = select_experience_units(
            units, requirement_matches, preference_adjustments, len(units)
        )
        reserve_pool = [u for u in full_sorted if u["id"] not in selected_ids]

        for u in selected_raw:
            log_preference_event(
                self.db, user_id=user_id, item_id=u["id"], job_id=job_id,
                action=PreferenceAction.SELECTED_BY_AI.value, job_context=job_context,
            )

        final_units, content, docx_bytes = self._rewrite_select_and_fit(
            selected_raw, reserve_pool, atoms, requirement_matches, must_have_requirements, basic_info,
            nice_to_have_requirements=nice_to_have_requirements,
            photo_path=_photo_path_for(user_id), job_title=job.title, job_domain=job.domain,
        )

        asset = self._save_resume_asset(None, user_id, job_id, content)
        path = write_resume_docx(docx_bytes, user_id, job_id, asset.file_path)
        asset.file_path = path
        self.db.flush()
        logger.info(
            "简历生成完成 user_id=%s job_id=%s 选中经历数=%s", user_id, job_id, len(final_units)
        )
        return asset

    # ------------------------------------------------------------------
    # 调整入口：用户增/删/排序后重新生成
    # ------------------------------------------------------------------
    def apply_selection_adjustment(
        self, user_id: int, job_id: int, unit_ids_in_order: list[int]
    ) -> GeneratedAsset:
        with llm_usage_context(user_id):
            return self._apply_selection_adjustment_impl(user_id, job_id, unit_ids_in_order)

    def _apply_selection_adjustment_impl(
        self, user_id: int, job_id: int, unit_ids_in_order: list[int]
    ) -> GeneratedAsset:
        """
        unit_ids_in_order 是用户调整后的最终经历顺序（增/删/排序合一，就是这个 id 列表本身）。
        跟原有选中集合 diff 出 removed/added 写偏好事件；为简单起见改写对新的全体选中经历
        重新跑一次，不复用旧 bullet——调整操作频率低，正确性优先于省成本。
        """
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")
        asset = self._get_resume_asset(user_id, job_id)
        if not asset:
            raise ValueError("尚未生成过简历，无法调整；请先调用生成接口")

        job_context = job.domain or "unknown"
        prev_ids = [
            e.get("_unit_id") for e in (asset.content_json or {}).get("experience") or []
            if e.get("_unit_id")
        ]
        prev_set, new_set = set(prev_ids), set(unit_ids_in_order)
        removed, added = prev_set - new_set, new_set - prev_set

        for uid in removed:
            log_preference_event(
                self.db, user_id=user_id, item_id=uid, job_id=job_id,
                action=PreferenceAction.REMOVED_BY_USER.value, job_context=job_context,
            )
        for uid in added:
            log_preference_event(
                self.db, user_id=user_id, item_id=uid, job_id=job_id,
                action=PreferenceAction.ADDED_BY_USER.value, job_context=job_context,
            )
        if not removed and not added and unit_ids_in_order != prev_ids:
            for uid in unit_ids_in_order:
                log_preference_event(
                    self.db, user_id=user_id, item_id=uid, job_id=job_id,
                    action=PreferenceAction.REORDERED.value, job_context=job_context,
                )

        units_by_id = {u["id"]: u for u in self._load_units(user_id)}
        new_units = [units_by_id[uid] for uid in unit_ids_in_order if uid in units_by_id]
        if not new_units:
            raise ValueError("调整后的经历列表为空或全部无效")

        score = self._get_score(user_id, job_id)
        requirement_matches = (score.requirement_matches if score else []) or []
        must_have_requirements = self._must_have_requirements(job)
        nice_to_have_requirements = self._nice_to_have_requirements(job)
        atoms = self._load_atoms(user_id)
        basic_info = self._basic_info_snapshot(user_id)

        rewrite_result = rewrite_experience_bullets(
            self.llm, new_units, must_have_requirements,
            min_bullets_per_unit=MIN_BULLETS_PER_EXPERIENCE, max_bullets_per_unit=MAX_BULLETS_PER_EXPERIENCE,
            max_chars_per_bullet=MAX_BULLET_CHARS,
            full_bullet_unit_ids=frozenset(u["id"] for u in new_units[:FULL_BULLET_TOP_N]),
            tailoring_mode=self.tailoring_mode,
            nice_to_have_requirements=nice_to_have_requirements,
            job_title=job.title, job_domain=job.domain,
        )
        bullets_by_unit = self._group_bullets(rewrite_result)
        polished = polish_bullets(self.llm, [b for bullets in bullets_by_unit.values() for b in bullets])
        bullets_by_unit = self._group_bullets({"bullets": polished})
        skills = select_skills(new_units, atoms, requirement_matches, MAX_SKILLS)
        experience_entries = [self._experience_entry(u, bullets_by_unit) for u in new_units]
        categories = categorize_skills(
            self.llm, skills, must_have_requirements,
            nice_to_have_requirements=nice_to_have_requirements,
            job_title=job.title, job_domain=job.domain,
        )
        content = {
            **basic_info,
            "experience": experience_entries,
            "skills": skills,
            "skill_categories": [
                {**c, "skills": _fit_line_to_one_line(c["label"], c.get("skills") or [], _SKILL_LINE_MAX_CHARS)}
                for c in categories
            ],
            "languages": _fit_line_to_one_line("Languages", self._select_languages(atoms), _SKILL_LINE_MAX_CHARS),
            "llm_model": self.llm.model_name,
        }

        docx_bytes = render_tailored_resume_docx(content, photo_path=_photo_path_for(user_id))
        # 调整也产生新版本；已投递记录仍指向投递瞬间的快照。
        saved = self._save_resume_asset(None, user_id, job_id, content)
        path = write_resume_docx(docx_bytes, user_id, job_id, saved.file_path)
        saved.file_path = path
        self.db.flush()
        return saved

    # ------------------------------------------------------------------
    # 选材 -> 改写 -> 渲染 -> 恰好一页还有空间就贪心回填
    # ------------------------------------------------------------------
    def _rewrite_select_and_fit(
        self,
        selected_raw: list[dict],
        reserve_pool: list[dict],
        atoms: list[dict],
        requirement_matches: list[dict],
        must_have_requirements: list[dict],
        basic_info: dict,
        nice_to_have_requirements: list[dict] | None = None,
        photo_path: str | None = None,
        job_title: str | None = None,
        job_domain: str | None = None,
    ) -> tuple[list[dict], dict, bytes]:
        # 确定性形状：generate_for_job 已经决定好选中几条经历（4 条=3-3-2-2，3 条=3-3-3），
        # 这里只管照着形状改写+渲染一次，不再"渲染-数页-调整"反复试探。4 条经历时最靠前
        # FULL_BULLET_TOP_N 条封顶写满、其余固定 2 条；3 条经历（或更少）时全部封顶写满。
        if len(selected_raw) == MAX_EXPERIENCE_ITEMS:
            full_bullet_unit_ids = frozenset(u["id"] for u in selected_raw[:FULL_BULLET_TOP_N])
            tail_bullets = MIN_BULLETS_PER_EXPERIENCE
        else:
            full_bullet_unit_ids = frozenset(u["id"] for u in selected_raw)
            tail_bullets = MAX_BULLETS_PER_EXPERIENCE  # 没有 tail 单元，这个值不会被用到

        rewrite_result = rewrite_experience_bullets(
            self.llm, selected_raw, must_have_requirements,
            min_bullets_per_unit=tail_bullets, max_bullets_per_unit=tail_bullets,
            full_bullet_count=MAX_BULLETS_PER_EXPERIENCE,
            max_chars_per_bullet=MAX_BULLET_CHARS,
            full_bullet_unit_ids=full_bullet_unit_ids,
            tailoring_mode=self.tailoring_mode,
            nice_to_have_requirements=nice_to_have_requirements,
            job_title=job_title, job_domain=job_domain,
        )
        bullets_by_unit = self._group_bullets(rewrite_result)
        languages = _fit_line_to_one_line("Languages", self._select_languages(atoms), _SKILL_LINE_MAX_CHARS)
        # 技能池随"当前包含哪些经历"变化（technologies 来自选中的经历单元），回填循环里
        # 同一个技能池可能重复出现——按技能池缓存分类结果，避免重复调 LLM
        skill_category_cache: dict[tuple, list[dict]] = {}

        def _categorize(skills: list[str]) -> list[dict]:
            key = tuple(skills)
            if key not in skill_category_cache:
                categories = categorize_skills(
                    self.llm, skills, must_have_requirements,
                    nice_to_have_requirements=nice_to_have_requirements,
                    job_title=job_title, job_domain=job_domain,
                )
                skill_category_cache[key] = [
                    {**c, "skills": _fit_line_to_one_line(c["label"], c.get("skills") or [], _SKILL_LINE_MAX_CHARS)}
                    for c in categories
                ]
            return skill_category_cache[key]

        def _render(units: list[dict]) -> tuple[dict, bytes]:
            # order_for_display 只影响展示顺序；砍/加经历按 priority 决定，是两回事
            ordered = order_for_display(units)
            skills = select_skills(ordered, atoms, requirement_matches, MAX_SKILLS)
            experience_entries = [self._experience_entry(u, bullets_by_unit) for u in ordered]
            c = {
                **basic_info,
                "experience": experience_entries,
                "skills": skills,
                "skill_categories": _categorize(skills),
                "languages": languages,
                "llm_model": self.llm.model_name,
            }
            return c, render_tailored_resume_docx(c, photo_path=photo_path)

        remaining = list(selected_raw)
        content, docx_bytes = _render(remaining)
        pages = count_pages(docx_bytes)
        if pages is not None and pages > 1:
            # 两种固定形状按经验值预算本该稳妥落在一页内；真出现这种情况就是内容本身
            # 异常长（比如公司名、bullet 远超预算），按原样输出，不再回退调整——形状判断
            # 已经把能省的空间都省了，没有更多杠杆可拉。
            logger.warning(
                "简历按固定形状（%s 条经历）渲染后仍超页（%s 页），按原样输出，不再回退调整",
                len(remaining), pages,
            )

        # 恰好一页（不是"没法验证"、也不是"仍然超页"）且候选池里还有没选进来的经历：
        # 按 priority 从高到低贪心往回加，加了还是一页就保留，加了变两页就撤销、到此为止。
        pool = list(reserve_pool)
        while pages == 1 and pool:
            candidate = pool.pop(0)
            extra = rewrite_experience_bullets(
                self.llm, [candidate], must_have_requirements,
                min_bullets_per_unit=MIN_BULLETS_PER_EXPERIENCE, max_bullets_per_unit=MAX_BULLETS_PER_EXPERIENCE,
                max_chars_per_bullet=MAX_BULLET_CHARS,
                tailoring_mode=self.tailoring_mode,
                nice_to_have_requirements=nice_to_have_requirements,
                job_title=job_title, job_domain=job_domain,
            )
            bullets_by_unit.update(self._group_bullets(extra))
            trial_units = remaining + [candidate]
            trial_content, trial_docx = _render(trial_units)
            trial_pages = count_pages(trial_docx)
            if trial_pages == 1:
                remaining, content, docx_bytes, pages = trial_units, trial_content, trial_docx, trial_pages
                logger.info("一页还有空间，回填经历 unit_id=%s", candidate["id"])
            else:
                logger.info(
                    "回填经历 unit_id=%s 会超页（%s 页），撤销，一页已尽量填满", candidate["id"], trial_pages
                )
                break

        # 最终选材/页数都定下来之后，对实际会上简历的 bullet 做一遍格式校对（补全成完整
        # 句子、可选加粗关键信息，见 ai.resume_rewrite.polish_bullets）——只对最终版本做
        # 一次，不在上面每次选材试探时都做，避免为试探性渲染反复调这次额外的 LLM。校对
        # 只改格式不改长度预算，理论上不会把页数校对坏；万一真的因为补全句子多了几个字
        # 导致超页，也不再回退重来（校对是收尾润色，不是选材决策，不值得为这个再跑一轮
        # 砍经历/回填），只记警告。
        remaining_ids = {u["id"] for u in remaining}
        to_polish = [b for uid in remaining_ids for b in bullets_by_unit.get(uid, [])]
        if to_polish:
            polished = polish_bullets(self.llm, to_polish)
            polished_by_unit = self._group_bullets({"bullets": polished})
            for uid in remaining_ids:
                if uid in polished_by_unit:
                    bullets_by_unit[uid] = polished_by_unit[uid]
            content, docx_bytes = _render(remaining)
            final_pages = count_pages(docx_bytes)
            if final_pages is not None and final_pages > 1:
                logger.warning("bullet 校对后简历超一页（%s 页），按原样输出，不再回退重来", final_pages)

        return remaining, content, docx_bytes

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def _load_units(self, user_id: int) -> list[dict]:
        rows = (
            self.db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.user_id == user_id)
            .order_by(UserExperienceUnit.order_index.asc(), UserExperienceUnit.id.asc())
            .all()
        )
        return [
            {
                "id": u.id,
                "title": u.title,
                "employer": u.employer,
                "background": u.background,
                "actions": u.actions,
                "technologies": u.technologies,
                "ownership": u.ownership,
                "results": u.results,
                "domain": u.domain,
                "start_date": u.start_date,
                "end_date": u.end_date,
                "raw_date_text": u.raw_date_text,
                "raw_text": u.raw_text,
                "tier": u.tier,
            }
            for u in rows
        ]

    def _load_atoms(self, user_id: int) -> list[dict]:
        facts = self.db.query(UserCandidateFacts).filter(UserCandidateFacts.user_id == user_id).first()
        return facts.atoms if facts else []

    def _basic_info_snapshot(self, user_id: int) -> dict:
        """固定基本信息（姓名/联系方式/教育背景）从 UserProfile.master_cv_json 拷贝快照，
        不运行时动态引用——跟 resume_persist.py 现有"一行可独立编辑"的模式一致。"""
        profile = self.db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        base = dict((profile.master_cv_json or {}) if profile else {})
        return {
            "full_name": base.get("full_name") or "",
            "location": base.get("location") or "",
            "phone": base.get("phone") or "",
            "email": base.get("email") or "",
            "visa": base.get("visa") or "",
            "linkedin": base.get("linkedin"),
            "github": base.get("github"),
            "profile_summary": base.get("profile_summary") or "",
            "education": base.get("education") or [],
        }

    def _get_score(self, user_id: int, job_id: int) -> UserJobScore | None:
        return (
            self.db.query(UserJobScore)
            .filter(UserJobScore.user_id == user_id, UserJobScore.job_id == job_id)
            .first()
        )

    def get_resume_asset(self, user_id: int, job_id: int) -> GeneratedAsset | None:
        """公开只读查询，供下载等接口直接用，不用重复写查询逻辑。"""
        return self._get_resume_asset(user_id, job_id)

    def _get_resume_asset(self, user_id: int, job_id: int) -> GeneratedAsset | None:
        return (
            self.db.query(GeneratedAsset)
            .filter(
                GeneratedAsset.user_id == user_id,
                GeneratedAsset.job_id == job_id,
                GeneratedAsset.asset_type == AssetType.RESUME_JSON.value,
            )
            .order_by(GeneratedAsset.id.desc())
            .first()
        )

    @staticmethod
    def _must_have_requirements(job: Job) -> list[dict]:
        requirements = (job.structured_requirements or {}).get("requirements") or []
        return [r for r in requirements if r.get("importance") == "must"]

    @staticmethod
    def _nice_to_have_requirements(job: Job) -> list[dict]:
        requirements = (job.structured_requirements or {}).get("requirements") or []
        return [r for r in requirements if r.get("importance") == "nice"]

    @staticmethod
    def _select_languages(atoms: list[dict]) -> list[str]:
        """口语能力（type=="language"，detail={"level": CEFR}）单独列一行，不并进 skills——
        对应模板 Skills 区块里独立的 "Languages: ..." 一行。跟 select_skills 一样不额外调 LLM，
        atoms 是 candidate_facts 的既有数据。

        English 保底：atoms 里没有语言数据的用户不在少数（一次性提取步骤没跑过/原简历没提/
        atom 被手动删掉了），这会导致 Languages 这一行直接消失。求职场景默认候选人英语流利
        是合理假设，所以没有 English atom 时补一条 "English (fluent)"；atoms 里已经有 English
        （不管什么等级）就尊重真实数据，不重复添加。"""
        out = []
        has_english = False
        for a in atoms or []:
            if a.get("type") != "language" or not a.get("label"):
                continue
            label = a["label"]
            if label.strip().lower() == "english":
                has_english = True
            level = (a.get("detail") or {}).get("level")
            out.append(f"{label} ({level})" if level else label)
        if not has_english:
            out.insert(0, "English (fluent)")
        return out

    @staticmethod
    def _group_bullets(rewrite_result: dict) -> dict[int, list[dict]]:
        bullets_by_unit: dict[int, list[dict]] = {}
        for b in rewrite_result.get("bullets") or []:
            bullets_by_unit.setdefault(b["unit_id"], []).append(b)
        return bullets_by_unit

    @staticmethod
    def _experience_entry(unit: dict, bullets_by_unit: dict[int, list[dict]]) -> dict:
        date_range = unit.get("raw_date_text") or ResumeGenerationService._format_date_range(
            unit.get("start_date"), unit.get("end_date")
        )
        bullets = [b["text"] for b in bullets_by_unit.get(unit["id"], [])]
        return {
            # 前缀下划线：内部记账用（供 apply_selection_adjustment 做 diff），docx 渲染忽略
            "_unit_id": unit["id"],
            "title": unit.get("title") or "",
            "company": unit.get("employer") or "",
            "location": "",
            "date_range": date_range,
            "bullets": bullets,
        }

    @staticmethod
    def _format_date_range(start: date | None, end: date | None) -> str:
        if not start:
            return ""
        s = start.strftime("%Y-%m")
        e = end.strftime("%Y-%m") if end else "Present"
        return f"{s} – {e}"

    def _save_resume_asset(
        self, existing: GeneratedAsset | None, user_id: int, job_id: int, content: dict
    ) -> GeneratedAsset:
        if existing:
            remove_file_if_exists(existing.file_path)
            existing.content_json = content
            existing.content_text = None
            existing.llm_model = self.llm.model_name
            existing.status = AssetStatus.DONE.value
            existing.file_path = None
            self.db.flush()
            return existing
        asset = GeneratedAsset(
            user_id=user_id,
            job_id=job_id,
            asset_type=AssetType.RESUME_JSON.value,
            content_json=content,
            content_text=None,
            storage_provider="local",
            llm_model=self.llm.model_name,
            status=AssetStatus.DONE.value,
            file_path=None,
        )
        try:
            self.db.add(asset)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            again = self._get_resume_asset(user_id, job_id)
            if again:
                return self._save_resume_asset(again, user_id, job_id, content)
            raise
        return asset
