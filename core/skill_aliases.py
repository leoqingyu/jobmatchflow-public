"""
技能别名归一化：写入 UserCandidateFacts.atoms 前调用，把同一技能的不同写法（大小写、缩写、
简繁/中英夹杂）折叠成一个规范 label，避免 "Postgres" 和 "PostgreSQL" 被算成两条独立事实。

只是个种子字典，不追求完备；遇到新的常见别名直接往 _ALIASES 里加一行即可，不需要建表——
现在没有"按技能查用户"这种查询需求，关系表是过度设计。
"""

from __future__ import annotations

_ALIASES: dict[str, str] = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "gcp": "Google Cloud Platform",
    "google cloud": "Google Cloud Platform",
    "google cloud platform": "Google Cloud Platform",
    "aws": "Amazon Web Services",
    "amazon web services": "Amazon Web Services",
    "azure": "Microsoft Azure",
    "microsoft azure": "Microsoft Azure",
    "ml": "Machine Learning",
    "machine learning": "Machine Learning",
    "nlp": "Natural Language Processing",
    "natural language processing": "Natural Language Processing",
    "sql server": "Microsoft SQL Server",
    "mssql": "Microsoft SQL Server",
    "mysql": "MySQL",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "react.js": "React",
    "reactjs": "React",
    "react": "React",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "node": "Node.js",
    "power bi": "Power BI",
    "powerbi": "Power BI",
    "excel": "Microsoft Excel",
    "ms excel": "Microsoft Excel",
}


def normalize_skill_label(raw: str) -> str:
    """未命中别名表时原样返回（首尾去空白），不强行改写用户没见过的写法。"""
    key = (raw or "").strip().lower()
    if not key:
        return (raw or "").strip()
    return _ALIASES.get(key, (raw or "").strip())
