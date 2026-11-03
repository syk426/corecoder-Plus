"""Skill system - on-demand knowledge injection for the agent.

Two-level loading architecture:
  - Layer 1: Skill catalog in system prompt (names + descriptions, cheap)
  - Layer 2: Full skill content loaded via load_skill tool (expensive, on demand)
"""

from pathlib import Path

import yaml


class SkillManager:
    """Manages skill registry and loading.

    Scans a skills directory for SKILL.md files, parses YAML frontmatter,
    and provides methods to list and load skills.
    """

    def __init__(self, skills_dir: Path):
        """Initialize SkillManager.

        Args:
            skills_dir: Directory containing skill subdirectories
        """
        self.skills_dir = skills_dir
        self.registry: dict[str, dict] = {}

    def scan_skills(self) -> None:
        """Scan skills directory and populate registry.

        Each skill is a subdirectory containing a SKILL.md file with YAML frontmatter.
        """
        if not self.skills_dir.exists():
            return

        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue

            raw = manifest.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(raw)

            name = meta.get("name", skill_dir.name)
            description = meta.get("description", "")

            self.registry[name] = {
                "name": name,
                "description": description,
                "content": raw,
            }

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from SKILL.md.

        Args:
            text: Raw SKILL.md content

        Returns:
            Tuple of (metadata dict, body content)
        """
        if not text.startswith("---"):
            return {}, text

        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text

        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            meta = {}

        return meta, parts[2].strip()

    def list_skills(self) -> str:
        """Generate skill catalog text for system prompt.

        Returns:
            Formatted list of skills with names and descriptions
        """
        if not self.registry:
            return "(no skills found)"

        lines = [
            f"- **{skill['name']}**: {skill['description']}"
            for skill in self.registry.values()
        ]
        return "\n".join(lines)

    def load_skill(self, name: str) -> str:
        """Load full skill content by name.

        Args:
            name: Skill name to load

        Returns:
            Full SKILL.md content or error message if not found
        """
        skill = self.registry.get(name)
        if not skill:
            return f"Skill not found: {name}"
        return skill["content"]
