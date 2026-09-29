from miniclaw.context.types import ContextSource
from miniclaw.skills.context import skills_context
from miniclaw.skills.matcher import SkillMatcher
from miniclaw.skills.types import Skill


class SkillContextProvider:
    """Select skills that match the prompt and expose them as context."""

    def __init__(
        self,
        skills: tuple[Skill, ...],
        *,
        matcher: SkillMatcher | None = None,
    ) -> None:
        self.skills = skills
        self.matcher = matcher or SkillMatcher()

    async def build(self, prompt: str) -> tuple[ContextSource, ...]:
        if not self.skills:
            return ()
        matches = self.matcher.match(prompt).select(self.skills)
        if not matches:
            return ()
        return (skills_context(matches),)
