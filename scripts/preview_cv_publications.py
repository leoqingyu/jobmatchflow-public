from __future__ import annotations

from pathlib import Path
from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _sample_payload() -> dict:
    return {
        "html_lang": "en",
        "full_name": "Alex Chen",
        "location": "Zurich, Switzerland",
        "phone": "+41 79 000 00 00",
        "email": "alex.chen@example.com",
        "visa": "B permit",
        "linkedin": {"label": "LinkedIn", "url": "https://linkedin.com/in/alexchen"},
        "github": {"label": "GitHub", "url": "https://github.com/alexchen"},
        "profile_image_base64": None,  # 有照片版模板可填 base64
        "profile_summary": (
            "Data scientist focused on credit risk and fraud detection. "
            "Experience building production ML pipelines, model monitoring, and stakeholder-ready analytics."
        ),
        "education": [
            {
                "degree": "MSc in Data Science",
                "institution": "ETH Zürich",
                "date_range": "2021 – 2023",
                "location": "Zürich",
                "subtitle": "Thesis: Counterfactual explanations for credit scoring",
                "bullets": [
                    "GPA: 5.6/6.0",
                    "Coursework: ML, Probabilistic Modeling, Time Series, Causal Inference",
                ],
            }
        ],
        "experience": [
            {
                "title": "Data Scientist (Risk Analytics)",
                "company": "FinTech Bank AG",
                "location": "Zurich",
                "date_range": "2023 – Present",
                "bullets": [
                    "Built a PD model with calibrated probabilities; improved AUC by 0.06 on holdout data.",
                    "Shipped a drift monitoring dashboard (Python, SQL, Airflow) used weekly by risk team.",
                ],
            },
            {
                "title": "Data Analyst (Intern)",
                "company": "Payments Co.",
                "location": "Remote",
                "date_range": "2022",
                "bullets": [
                    "Designed KPI definitions and anomaly alerts for chargeback rates.",
                ],
            },
        ],
        # 新字段：Publications（模板也兼容旧 projects）
        "publications": [
            {
                "title": "Interpretable Credit Scoring via Monotonic GBMs",
                "venue": "NeurIPS Workshop on Finance",
                "date": "2024",
                "link": "https://arxiv.org/abs/2401.00001",
                "highlights": [
                    "Proposed monotonic constraints + calibration for regulatory-friendly scorecards.",
                    "Released reproducible training pipeline with ablations on public credit datasets.",
                ],
            },
            {
                "title": "Fraud Detection with Graph Neural Networks under Concept Drift",
                "venue": "KDD (Applied Data Science track)",
                "date": "2023",
                "url": "https://example.com/paper",
                "bullets": [
                    "Developed online evaluation protocol; reduced false positives by 12% at fixed recall.",
                ],
            },
        ],
        "skills": [
            {"category": "Languages", "items": "Python, SQL"},
            {"category": "ML", "items": "XGBoost, LightGBM, sklearn, calibration, SHAP"},
            {"category": "Data", "items": "dbt, Airflow, PostgreSQL, BigQuery"},
        ],
    }


def render_one(template_name: str, out_path: Path) -> None:
    tpl_dir = Path(__file__).resolve().parents[1] / "renderer" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(tpl_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template(template_name)
    html = tpl.render(**_sample_payload())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "tmp"
    stamp = date.today().isoformat()
    render_one(
        "cv_templet_without_photo.html",
        out_dir / f"cv_preview_publications_without_photo_{stamp}.html",
    )
    render_one(
        "cv_templet.html",
        out_dir / f"cv_preview_publications_with_photo_{stamp}.html",
    )
    print(f"已生成预览：{out_dir}")


if __name__ == "__main__":
    main()

