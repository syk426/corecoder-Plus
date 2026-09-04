"""Tests for skill system."""

import pytest
from pathlib import Path
import tempfile
import shutil

from corecoder.skills import SkillManager


@pytest.fixture
def temp_skills_dir():
    """Create a temporary skills directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_skill(temp_skills_dir):
    """Create a sample skill for testing."""
    skill_dir = temp_skills_dir / "test-skill"
    skill_dir.mkdir()

    skill_content = """---
name: test-skill
description: A test skill for unit testing
---

# Test Skill

This is a test skill content.

## Instructions

1. Do something
2. Do something else
"""
    (skill_dir / "SKILL.md").write_text(skill_content)
    return skill_dir


def test_skill_manager_init(temp_skills_dir):
    """Test SkillManager initialization."""
    manager = SkillManager(temp_skills_dir)
    assert manager.skills_dir == temp_skills_dir
    assert manager.registry == {}


def test_scan_skills_empty(temp_skills_dir):
    """Test scanning empty skills directory."""
    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()
    assert manager.registry == {}


def test_scan_skills_with_skill(temp_skills_dir, sample_skill):
    """Test scanning skills directory with a skill."""
    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert "test-skill" in manager.registry
    skill = manager.registry["test-skill"]
    assert skill["name"] == "test-skill"
    assert skill["description"] == "A test skill for unit testing"
    assert "# Test Skill" in skill["content"]


def test_parse_frontmatter_valid():
    """Test parsing valid YAML frontmatter."""
    text = """---
name: my-skill
description: My skill description
---

# My Skill

Content here.
"""
    meta, body = SkillManager._parse_frontmatter(text)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "My skill description"
    assert "# My Skill" in body


def test_parse_frontmatter_no_frontmatter():
    """Test parsing text without frontmatter."""
    text = """# Just a markdown file

No frontmatter here.
"""
    meta, body = SkillManager._parse_frontmatter(text)
    assert meta == {}
    assert "# Just a markdown file" in body


def test_parse_frontmatter_invalid_yaml():
    """Test parsing invalid YAML frontmatter."""
    text = """---
invalid: yaml: content: [
---

Content.
"""
    meta, body = SkillManager._parse_frontmatter(text)
    assert meta == {}  # Should handle gracefully
    assert "Content." in body


def test_list_skills_empty(temp_skills_dir):
    """Test listing skills when none exist."""
    manager = SkillManager(temp_skills_dir)
    result = manager.list_skills()
    assert result == "(no skills found)"


def test_list_skills_with_skills(temp_skills_dir, sample_skill):
    """Test listing skills when skills exist."""
    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    result = manager.list_skills()
    assert "test-skill" in result
    assert "A test skill for unit testing" in result


def test_load_skill_exists(temp_skills_dir, sample_skill):
    """Test loading an existing skill."""
    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    content = manager.load_skill("test-skill")
    assert "# Test Skill" in content
    assert "This is a test skill content." in content


def test_load_skill_not_exists(temp_skills_dir):
    """Test loading a non-existent skill."""
    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    content = manager.load_skill("non-existent")
    assert content == "Skill not found: non-existent"


def test_multiple_skills(temp_skills_dir):
    """Test scanning multiple skills."""
    # Create first skill
    skill1_dir = temp_skills_dir / "skill-one"
    skill1_dir.mkdir()
    (skill1_dir / "SKILL.md").write_text(
        "---\nname: skill-one\ndescription: First skill\n---\n# Skill One"
    )

    # Create second skill
    skill2_dir = temp_skills_dir / "skill-two"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text(
        "---\nname: skill-two\ndescription: Second skill\n---\n# Skill Two"
    )

    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert len(manager.registry) == 2
    assert "skill-one" in manager.registry
    assert "skill-two" in manager.registry

    listing = manager.list_skills()
    assert "skill-one" in listing
    assert "skill-two" in listing
    assert "First skill" in listing
    assert "Second skill" in listing


def test_skill_without_description(temp_skills_dir):
    """Test skill without description in frontmatter."""
    skill_dir = temp_skills_dir / "no-desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: no-desc\n---\n# No Description")

    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert "no-desc" in manager.registry
    skill = manager.registry["no-desc"]
    assert skill["name"] == "no-desc"
    assert skill["description"] == ""


def test_skill_name_from_directory(temp_skills_dir):
    """Test skill name defaults to directory name if not in frontmatter."""
    skill_dir = temp_skills_dir / "dir-named-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: Some desc\n---\n# Content")

    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert "dir-named-skill" in manager.registry
    assert manager.registry["dir-named-skill"]["name"] == "dir-named-skill"


def test_non_skill_directory_ignored(temp_skills_dir):
    """Test that directories without SKILL.md are ignored."""
    # Create a directory without SKILL.md
    no_skill_dir = temp_skills_dir / "no-skill-here"
    no_skill_dir.mkdir()
    (no_skill_dir / "README.md").write_text("# Not a skill")

    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert len(manager.registry) == 0


def test_non_directory_ignored(temp_skills_dir):
    """Test that non-directory files are ignored."""
    # Create a file (not directory) in skills dir
    (temp_skills_dir / "random-file.txt").write_text("Not a skill")

    manager = SkillManager(temp_skills_dir)
    manager.scan_skills()

    assert len(manager.registry) == 0
