"""load_skill tool - load full skill content on demand."""

from .base import Tool


class LoadSkillTool(Tool):
    """Tool for loading full skill content by name."""

    name = "load_skill"
    description = "Load the full content of a skill by name. Use this to get detailed instructions for a specific skill."
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the skill to load",
            }
        },
        "required": ["name"],
    }

    def __init__(self, skill_manager):
        """Initialize LoadSkillTool.

        Args:
            skill_manager: SkillManager instance for loading skills
        """
        self.skill_manager = skill_manager

    def execute(self, name: str) -> str:
        """Load and return full skill content.

        Args:
            name: Skill name to load

        Returns:
            Full SKILL.md content or error message
        """
        return self.skill_manager.load_skill(name)
