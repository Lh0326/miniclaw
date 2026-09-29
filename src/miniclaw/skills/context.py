from miniclaw.context.types import ContextSource
from miniclaw.skills.types import SkillMatch


def skills_context(matches: tuple[SkillMatch, ...]) -> ContextSource:
    text = "\n".join(
        f'<skill name="{match.skill.name}" source="{match.skill.source_id}">\n'
        f"{match.skill.instructions}\n"
        "</skill>"
        for match in matches
    )
    return ContextSource("skills", text, priority=95, required=False)
