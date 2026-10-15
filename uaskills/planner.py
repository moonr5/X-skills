"""Lexical task planning. This selects skills and writes a plan; it does not execute them."""

from __future__ import annotations

from typing import Any

from uaskills.catalog import search_skills
from uaskills.library import Library
from uaskills.textutil import tokenize


def plan_task(
    library: Library,
    task: str,
    *,
    runtime: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    keywords = tokenize(task)
    categories = _likely_categories(library, keywords)
    fetched = search_skills(
        library.conn,
        query=task,
        limit=min(50, max(limit * 6, 20)),
        offset=0,
        include_synthetic=False,
    )
    ranked = list(fetched["results"])
    if categories:
        preferred = [item for item in ranked if item.get("category") in categories]
        others = [item for item in ranked if item.get("category") not in categories]
        ranked = preferred + others
    selected = []
    rejected = []
    covered: set[str] = set()
    warnings = []
    for candidate in ranked:
        decision = _compatibility(candidate, runtime)
        if decision == "reject":
            rejected.append(
                {
                    "id": candidate["id"],
                    "name": candidate.get("name"),
                    "reason": f"Declared runtimes do not include {runtime}.",
                }
            )
            continue
        tokens = set(
            tokenize(
                " ".join(
                    [
                        candidate.get("name") or "",
                        candidate.get("description") or "",
                        " ".join(candidate.get("tags") or []),
                        " ".join(candidate.get("capabilities") or []),
                    ]
                )
            )
        )
        if selected and tokens and len(tokens - covered) == 0:
            rejected.append(
                {
                    "id": candidate["id"],
                    "name": candidate.get("name"),
                    "reason": "Overlaps a higher-ranked selected skill.",
                }
            )
            continue
        selected.append(candidate)
        covered.update(tokens)
        note = _warning(candidate, runtime)
        if note:
            warnings.append(note)
        if len(selected) >= limit:
            break
    if not selected and not library.counts()["skills"]:
        warnings.append("The catalog has no imported skills, so nothing could be selected.")
    plan = {
        "task": task,
        "task_interpretation": {
            "raw_task": task,
            "keywords": keywords,
            "likely_categories": categories,
            "method": "lexical",
            "acceptance_criteria": (
                "No separate acceptance criteria were stated. "
                "Use the task text itself, plus any checks written inside the selected skills."
            ),
        },
        "selected_skills": [_selected(item, task) for item in selected],
        "considered_but_rejected": rejected,
        "compatibility_warnings": warnings,
        "required_tools": _union(selected, "required_tools"),
        "dependencies": _union(selected, "dependencies"),
        "execution_sequence": _sequence(selected),
        "verification_steps": [
            "Compare the output with the task text.",
            "Run the project's tests or the checks named inside each selected skill.",
            "Inspect files that were created or modified.",
            "Report failures and anything the selected skills do not cover.",
        ],
        "limitations": _limitations(runtime, selected),
        "execution_performed": False,
    }
    return plan


def _likely_categories(library: Library, keywords: list[str]) -> list[str]:
    scores: dict[str, int] = {}
    keyword_set = set(keywords)
    text = " ".join(keywords)
    for item in library.category_config["categories"]:
        if item["id"] == "other":
            continue
        score = 0
        for word in item.get("keywords") or []:
            if " " in word and word in text:
                score += 1
            elif word in keyword_set:
                score += 1
        if score:
            scores[item["id"]] = score
    if not scores:
        return []
    best = max(scores.values())
    return [cid for cid, score in scores.items() if score == best]


def _compatibility(skill: dict[str, Any], runtime: str | None) -> str:
    if not runtime:
        return "ok"
    declared = [str(item).lower() for item in (skill.get("supported_runtimes") or [])]
    if declared and runtime.lower() not in declared:
        return "reject"
    if not declared:
        return "warn"
    return "ok"


def _warning(skill: dict[str, Any], runtime: str | None) -> str | None:
    name = skill.get("name") or skill["id"]
    messages = []
    if runtime and not skill.get("supported_runtimes"):
        messages.append(f"{name} does not declare a runtime; compatibility with {runtime} is unknown.")
    if skill.get("license_status") == "unknown":
        messages.append(f"{name} has no recorded license.")
    findings = skill.get("security_findings") or []
    if findings:
        messages.append(f"{name} has {len(findings)} heuristic security finding(s). A scan is not proof of safety.")
    if not messages:
        return None
    return " ".join(messages)


def _selected(skill: dict[str, Any], task: str) -> dict[str, Any]:
    matched = [
        token
        for token in tokenize(task)
        if token in tokenize(" ".join([skill.get("name") or "", skill.get("description") or "", skill.get("body_excerpt") or ""]))
    ]
    reason_parts = []
    if matched:
        reason_parts.append("matched terms: " + ", ".join(matched[:8]))
    reason_parts.append(f"category {skill.get('category') or 'unknown'}")
    reason_parts.append(f"license status {skill.get('license_status')}")
    reason_parts.append(f"verification {skill.get('verification_status')}")
    if skill.get("description"):
        reason_parts.append("description was declared by the skill")
    return {
        "id": skill["id"],
        "name": skill.get("name"),
        "description": skill.get("description"),
        "local_path": skill.get("local_path"),
        "source_url": skill.get("source_url"),
        "commit_sha": skill.get("commit_sha"),
        "version": skill.get("version"),
        "category": skill.get("category"),
        "license_status": skill.get("license_status"),
        "verification_status": skill.get("verification_status"),
        "lifecycle_status": skill.get("lifecycle_status"),
        "reason": "; ".join(reason_parts),
    }


def _union(skills: list[dict[str, Any]], field: str) -> list[str]:
    values = []
    seen = set()
    for skill in skills:
        for item in skill.get(field) or []:
            if item not in seen:
                seen.add(item)
                values.append(item)
    return values


def _sequence(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = [
        {
            "step": 1,
            "action": "Read only the selected skill instructions.",
            "skills": [skill["id"] for skill in skills],
        }
    ]
    for index, skill in enumerate(skills, start=2):
        steps.append(
            {
                "step": index,
                "action": "If the connected agent has the required tools, follow this skill. Do not claim it ran if it was only read.",
                "skills": [skill["id"]],
                "local_path": skill.get("local_path"),
            }
        )
    steps.append(
        {
            "step": len(steps) + 1,
            "action": "Verify the result and record optional local feedback. Do not promote the skill to verified from one run.",
            "skills": [skill["id"] for skill in skills],
        }
    )
    return steps


def _limitations(runtime: str | None, selected: list[dict[str, Any]]) -> list[str]:
    notes = [
        "This plan does not execute skills or external tools.",
        "Selection uses full-text and keyword overlap. It is not a semantic judgment that a skill will succeed.",
        "Imported skills are untrusted until a person assesses them.",
        "License status records what was written down. It is not a legal review.",
        "Security screening is a heuristic. It does not prove that a skill is safe.",
        "Do not load the rest of the catalog into context.",
    ]
    if runtime:
        notes.append(f"Requested runtime filter: {runtime}. Skills with no declared runtime were kept and warned.")
    if not selected:
        notes.append("No skill was selected. Import or search with a more specific query before assuming a capability exists.")
    return notes
