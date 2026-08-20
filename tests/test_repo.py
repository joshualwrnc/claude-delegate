#!/usr/bin/env python3
"""Tests for the validator, the packager, and install.sh.

Standard library only (unittest), so CI needs no install step:

    python3 -m unittest discover -s tests -v

This file contains deliberate absolute-path strings as test input for the
leaked-path check, so it opts out of that scan: LEAK-CHECK-EXEMPT
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import package_skill  # noqa: E402
import validate_skill  # noqa: E402
from validate_skill import validate  # noqa: E402

GOOD_DESC = "Does a thing. Use when the user asks for the thing."
GOOD_BODY = "# Delegate\n\nSome instructions.\n"


def write_skill(
    root: Path,
    *,
    name: str = "delegate",
    dirname: str | None = None,
    frontmatter: str | None = None,
    body: str = GOOD_BODY,
) -> Path:
    """Create a minimal skill directory under root and return its path."""
    skill_dir = root / (dirname or name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = f"name: {name}\ndescription: {GOOD_DESC}"
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return skill_dir


def write_evals(skill_dir: Path, payload: object | str) -> None:
    evals_dir = skill_dir / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    (evals_dir / "evals.json").write_text(text, encoding="utf-8")


def minimal_evals(skill_name: str = "delegate", prompt: str = "do a thing") -> dict:
    return {
        "skill_name": skill_name,
        "evals": [
            {"id": 0, "eval_name": "a", "prompt": prompt, "expected_output": "x", "files": []}
        ],
    }


class TempRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def run_validate(self, skill_dir: Path, **kwargs):
        kwargs.setdefault("repo_root", self.tmp)
        kwargs.setdefault("scan_repo", False)
        return validate(skill_dir, **kwargs)

    def assertErrorMatching(self, report, needle: str) -> None:
        joined = "\n".join(report.errors)
        self.assertIn(
            needle, joined, f"expected an error containing {needle!r}, got: {joined or '(none)'}"
        )


class TestRealRepo(TempRepoTest):
    """The shipped skill must pass under both rule sets."""

    def test_real_skill_passes_local_rules(self) -> None:
        report = validate(REPO_ROOT / "delegate")
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")

    def test_real_skill_passes_upload_rules(self) -> None:
        report = validate(REPO_ROOT / "delegate", for_upload=True)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")

    def test_real_skill_has_no_warnings(self) -> None:
        report = validate(REPO_ROOT / "delegate")
        self.assertEqual([], report.warnings)

    def test_fixture_shared_never_rules_are_exactly_two(self) -> None:
        """Eval case 3's ground truth. If this drifts, the eval is wrong."""
        brands = REPO_ROOT / "delegate/evals/fixtures/brands"
        rule_sets = []
        for guide in sorted(brands.glob("*/brand-guide.md")):
            rules = set()
            in_section = False
            for line in guide.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    in_section = line.strip() == "## Never rules"
                elif in_section and line.startswith("- "):
                    rules.add(line[2:].strip())
            rule_sets.append(rules)

        self.assertEqual(3, len(rule_sets), "expected three brand guides")
        shared = set.intersection(*rule_sets)
        self.assertEqual(
            {
                "Never make performance or safety claims we cannot cite.",
                "Never use ALL-CAPS for emphasis.",
            },
            shared,
        )

        # The near-miss must be in exactly two guides, or the eval stops
        # discriminating between a real read and a lucky guess.
        near_miss = "Never promise a delivery date we do not control."
        self.assertEqual(2, sum(near_miss in rules for rules in rule_sets))

    def test_render_honours_redirected_streams(self) -> None:
        """render() must resolve sys.stdout/stderr at call time, not at def time."""
        report = validate_skill.Report()
        report.error("boom")
        report.warn("careful")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            report.render()
        self.assertIn("careful", out.getvalue())
        self.assertIn("boom", err.getvalue())

    def test_report_state_does_not_leak_between_runs(self) -> None:
        first = validate(REPO_ROOT / "delegate")
        second = validate(REPO_ROOT / "delegate")
        self.assertEqual(first.errors, second.errors)
        self.assertTrue(second.ok)


class TestFrontmatter(TempRepoTest):
    def test_missing_name(self) -> None:
        skill = write_skill(self.tmp, frontmatter=f"description: {GOOD_DESC}")
        self.assertErrorMatching(self.run_validate(skill), "missing required key 'name'")

    def test_missing_description(self) -> None:
        skill = write_skill(self.tmp, frontmatter="name: delegate")
        self.assertErrorMatching(self.run_validate(skill), "missing required key 'description'")

    def test_name_directory_mismatch(self) -> None:
        skill = write_skill(self.tmp, name="other", dirname="delegate")
        self.assertErrorMatching(self.run_validate(skill), "does not match directory")

    def test_name_not_kebab_case(self) -> None:
        skill = write_skill(self.tmp, name="Delegate", dirname="Delegate")
        self.assertErrorMatching(self.run_validate(skill), "must be kebab-case")

    def test_name_with_consecutive_hyphens(self) -> None:
        skill = write_skill(self.tmp, name="de--legate", dirname="de--legate")
        self.assertErrorMatching(self.run_validate(skill), "cannot start/end with a hyphen")

    def test_name_too_long(self) -> None:
        long_name = "a" * 65
        skill = write_skill(self.tmp, name=long_name, dirname=long_name)
        self.assertErrorMatching(self.run_validate(skill), "limit is 64")

    def test_description_with_angle_brackets(self) -> None:
        # The Skills API rejects these outright.
        skill = write_skill(self.tmp, frontmatter="name: delegate\ndescription: Use <this> now")
        self.assertErrorMatching(self.run_validate(skill), "angle brackets")

    def test_description_too_long(self) -> None:
        skill = write_skill(
            self.tmp, frontmatter="name: delegate\ndescription: " + "a" * 1025
        )
        self.assertErrorMatching(self.run_validate(skill), "limit is 1024")

    def test_compatibility_too_long(self) -> None:
        frontmatter = (
            f"name: delegate\ndescription: {GOOD_DESC}\ncompatibility: " + "a" * 501
        )
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertErrorMatching(self.run_validate(skill), "limit is 500")

    def test_compatibility_within_limit_is_accepted(self) -> None:
        frontmatter = (
            f"name: delegate\ndescription: {GOOD_DESC}\ncompatibility: Requires Claude Code."
        )
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertTrue(self.run_validate(skill).ok)

    def test_no_frontmatter(self) -> None:
        skill_dir = self.tmp / "delegate"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Delegate\n", encoding="utf-8")
        self.assertErrorMatching(self.run_validate(skill_dir), "must start with a '---'")

    def test_unclosed_frontmatter(self) -> None:
        skill_dir = self.tmp / "delegate"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: delegate\ndescription: {GOOD_DESC}\n\n# Body\n", encoding="utf-8"
        )
        self.assertErrorMatching(self.run_validate(skill_dir), "never closed")

    def test_nested_frontmatter_is_rejected_not_misparsed(self) -> None:
        frontmatter = f"name: delegate\ndescription: {GOOD_DESC}\nmetadata:\n  key: value"
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertErrorMatching(self.run_validate(skill), "nested/multi-line frontmatter")

    def test_missing_skill_md(self) -> None:
        skill_dir = self.tmp / "delegate"
        skill_dir.mkdir()
        self.assertErrorMatching(self.run_validate(skill_dir), "SKILL.md: missing")

    def test_blank_lines_in_frontmatter_are_ignored(self) -> None:
        skill = write_skill(self.tmp, frontmatter=f"name: delegate\n\ndescription: {GOOD_DESC}")
        self.assertTrue(self.run_validate(skill).ok)

    def test_line_without_colon_is_rejected(self) -> None:
        frontmatter = f"name: delegate\ndescription: {GOOD_DESC}\nbogus"
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertErrorMatching(self.run_validate(skill), "expected 'key: value'")

    def test_duplicate_key(self) -> None:
        frontmatter = f"name: delegate\ndescription: {GOOD_DESC}\nname: delegate"
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertErrorMatching(self.run_validate(skill), "duplicate frontmatter key")

    def test_unknown_key_is_error(self) -> None:
        frontmatter = f"name: delegate\ndescription: {GOOD_DESC}\nbogus: 1"
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertErrorMatching(self.run_validate(skill), "unrecognized frontmatter key")

    def test_local_only_key_passes_locally_but_fails_for_upload(self) -> None:
        frontmatter = (
            f"name: delegate\ndescription: {GOOD_DESC}\ndisable-model-invocation: true"
        )
        skill = write_skill(self.tmp, frontmatter=frontmatter)
        self.assertTrue(self.run_validate(skill).ok)
        self.assertErrorMatching(
            self.run_validate(skill, for_upload=True), "rejected on upload"
        )

    def test_empty_body(self) -> None:
        skill = write_skill(self.tmp, body="")
        self.assertErrorMatching(self.run_validate(skill), "body is empty")

    def test_body_without_heading_warns_only(self) -> None:
        skill = write_skill(self.tmp, body="just prose, no heading\n")
        report = self.run_validate(skill)
        self.assertTrue(report.ok)
        self.assertTrue(any("no top-level" in w for w in report.warnings))


class TestNestedSkillMd(TempRepoTest):
    def test_nested_packaged_skill_md_is_error(self) -> None:
        skill = write_skill(self.tmp)
        nested = skill / "references" / "sub"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("---\nname: x\n---\n# x\n", encoding="utf-8")
        self.assertErrorMatching(self.run_validate(skill), "exactly one")

    def test_nested_skill_md_under_evals_is_allowed(self) -> None:
        """Fixtures live under evals/, which is never packaged."""
        skill = write_skill(self.tmp)
        nested = skill / "evals" / "fixtures" / "skills" / "toy"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("---\nname: toy\n---\n# toy\n", encoding="utf-8")
        write_evals(skill, minimal_evals())
        self.assertTrue(self.run_validate(skill).ok)

    def test_nested_skill_md_under_pycache_is_allowed(self) -> None:
        skill = write_skill(self.tmp)
        nested = skill / "__pycache__"
        nested.mkdir()
        (nested / "SKILL.md").write_text("junk", encoding="utf-8")
        self.assertTrue(self.run_validate(skill).ok)


class TestEvals(TempRepoTest):
    def test_missing_evals_only_warns(self) -> None:
        skill = write_skill(self.tmp)
        report = self.run_validate(skill)
        self.assertTrue(report.ok)
        self.assertTrue(any("no eval suite" in w for w in report.warnings))

    def test_invalid_json(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, "{not json")
        self.assertErrorMatching(self.run_validate(skill), "invalid JSON")

    def test_skill_name_mismatch(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(skill_name="wrong"))
        self.assertErrorMatching(self.run_validate(skill), "!= directory")

    def test_empty_eval_list(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, {"skill_name": "delegate", "evals": []})
        self.assertErrorMatching(self.run_validate(skill), "non-empty list")

    def test_duplicate_ids(self) -> None:
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"].append(
            {"id": 0, "eval_name": "b", "prompt": "p", "expected_output": "x"}
        )
        write_evals(skill, payload)
        self.assertErrorMatching(self.run_validate(skill), "duplicate id")

    def test_duplicate_names(self) -> None:
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"].append(
            {"id": 1, "eval_name": "a", "prompt": "p", "expected_output": "x"}
        )
        write_evals(skill, payload)
        self.assertErrorMatching(self.run_validate(skill), "duplicate eval_name")

    def test_missing_required_field(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, {"skill_name": "delegate", "evals": [{"id": 0, "prompt": "p"}]})
        self.assertErrorMatching(self.run_validate(skill), "missing required field")

    def test_id_zero_is_not_treated_as_missing(self) -> None:
        """id 0 is falsy — the check must not reject it."""
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals())
        errors = "\n".join(self.run_validate(skill).errors)
        self.assertNotIn("missing required field 'id'", errors)

    def test_missing_fixture_path_is_error(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(prompt="read delegate/evals/fixtures/nope.md please"))
        self.assertErrorMatching(self.run_validate(skill), "missing path")

    def test_existing_fixture_path_passes(self) -> None:
        skill = write_skill(self.tmp)
        fixture = self.tmp / "delegate" / "evals" / "fixtures" / "real.md"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("hi", encoding="utf-8")
        write_evals(skill, minimal_evals(prompt="read delegate/evals/fixtures/real.md please"))
        self.assertTrue(self.run_validate(skill).ok)

    def test_trailing_punctuation_is_stripped_from_path(self) -> None:
        skill = write_skill(self.tmp)
        fixture = self.tmp / "delegate" / "evals" / "fixtures" / "real.md"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("hi", encoding="utf-8")
        write_evals(skill, minimal_evals(prompt="see delegate/evals/fixtures/real.md."))
        self.assertTrue(self.run_validate(skill).ok)

    def test_directory_reference_passes(self) -> None:
        skill = write_skill(self.tmp)
        target = self.tmp / "delegate" / "evals" / "fixtures" / "dir"
        target.mkdir(parents=True)
        write_evals(skill, minimal_evals(prompt="sweep delegate/evals/fixtures/dir/"))
        self.assertTrue(self.run_validate(skill).ok)

    def test_url_containing_repo_name_is_not_a_path_reference(self) -> None:
        """github.com/user/claude-delegate/issues/5 is not 'delegate/issues/5'."""
        skill = write_skill(self.tmp)
        write_evals(
            skill,
            minimal_evals(prompt="see https://github.com/someone/claude-delegate/issues/5"),
        )
        self.assertTrue(self.run_validate(skill).ok, self.run_validate(skill).errors)

    def test_nested_path_ending_in_skill_dir_is_not_a_path_reference(self) -> None:
        """out/delegate/summary.md must not be read as delegate/summary.md."""
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(prompt="write results to out/delegate/summary.md"))
        self.assertTrue(self.run_validate(skill).ok, self.run_validate(skill).errors)

    def test_dotted_prefix_is_not_a_path_reference(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(prompt="module pkg.tests/helper.py is unrelated"))
        self.assertTrue(self.run_validate(skill).ok, self.run_validate(skill).errors)

    def test_real_path_after_punctuation_is_still_checked(self) -> None:
        """The lookbehind must not make the check miss genuine references."""
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(prompt="(delegate/evals/fixtures/gone.md)"))
        self.assertErrorMatching(self.run_validate(skill), "missing path")

    def test_files_list_paths_are_checked(self) -> None:
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"][0]["files"] = ["delegate/evals/fixtures/gone.md"]
        write_evals(skill, payload)
        self.assertErrorMatching(self.run_validate(skill), "missing path")

    def test_files_list_with_existing_path_passes(self) -> None:
        skill = write_skill(self.tmp)
        fixture = self.tmp / "delegate" / "evals" / "fixtures" / "real.md"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("hi", encoding="utf-8")
        payload = minimal_evals()
        payload["evals"][0]["files"] = ["delegate/evals/fixtures/real.md"]
        write_evals(skill, payload)
        self.assertTrue(self.run_validate(skill).ok)

    def test_non_object_eval_case_is_rejected(self) -> None:
        skill = write_skill(self.tmp)
        write_evals(skill, {"skill_name": "delegate", "evals": ["just a string"]})
        self.assertErrorMatching(self.run_validate(skill), "expected an object")

    def test_files_must_be_a_list(self) -> None:
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"][0]["files"] = "not-a-list"
        write_evals(skill, payload)
        self.assertErrorMatching(self.run_validate(skill), "'files' must be a list")

    def test_files_entries_must_be_strings(self) -> None:
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"][0]["files"] = [42]
        write_evals(skill, payload)
        self.assertErrorMatching(self.run_validate(skill), "must be strings")


class TestLeakScan(TempRepoTest):
    def _scan(self) -> validate_skill.Report:
        report = validate_skill.Report()
        validate_skill.check_no_leaked_paths(report, self.tmp)
        return report

    def test_detects_linux_home_path(self) -> None:
        (self.tmp / "notes.md").write_text("see /home/someone/work/file.md", encoding="utf-8")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_detects_macos_home_path(self) -> None:
        (self.tmp / "notes.md").write_text("see /Users/someone/work/file.md", encoding="utf-8")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_exempt_token_suppresses(self) -> None:
        (self.tmp / "notes.md").write_text(
            "LEAK-CHECK-EXEMPT\nsee /home/someone/x.md", encoding="utf-8"
        )
        self.assertTrue(self._scan().ok)

    def test_ignores_binary_and_unknown_suffixes(self) -> None:
        (self.tmp / "blob.bin").write_bytes(b"/home/someone/secret")
        self.assertTrue(self._scan().ok)

    def test_ignores_dist_directory(self) -> None:
        dist = self.tmp / "dist"
        dist.mkdir()
        (dist / "notes.md").write_text("/home/someone/x", encoding="utf-8")
        self.assertTrue(self._scan().ok)

    def test_ignores_undecodable_text_file(self) -> None:
        """A .md that is not valid UTF-8 is skipped, not a crash."""
        (self.tmp / "notes.md").write_bytes(b"\xff\xfe/home/someone/secret")
        self.assertTrue(self._scan().ok)

    def test_clean_tree_passes(self) -> None:
        (self.tmp / "notes.md").write_text("relative/path/is/fine.md", encoding="utf-8")
        self.assertTrue(self._scan().ok)

    def test_detects_home_path_without_trailing_slash(self) -> None:
        """A bare /home/<name> at end of line is the same leak."""
        (self.tmp / "notes.md").write_text("export BASE=/home/someone", encoding="utf-8")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_detects_macos_home_path_without_trailing_slash(self) -> None:
        (self.tmp / "notes.json").write_text('{"home": "/Users/someone"}', encoding="utf-8")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_validator_source_does_not_exempt_itself(self) -> None:
        """The exemption token must not appear contiguously in the validator.

        It defines the token, so a naive literal would make the validator skip
        its own source and never report a leak in it.
        """
        source = (REPO_ROOT / "scripts" / "validate_skill.py").read_text(encoding="utf-8")
        self.assertNotIn(validate_skill.LEAK_EXEMPT_TOKEN, source)


class TestPackager(TempRepoTest):
    def package_real(self) -> Path:
        out = self.tmp / "dist"
        return package_skill.package(REPO_ROOT / "delegate", out, quiet=True)

    def test_produces_skill_archive(self) -> None:
        archive = self.package_real()
        self.assertTrue(archive.is_file())
        self.assertEqual("delegate.skill", archive.name)

    def test_entries_rooted_at_skill_folder(self) -> None:
        with zipfile.ZipFile(self.package_real()) as zf:
            names = zf.namelist()
        self.assertTrue(names)
        for name in names:
            self.assertTrue(name.startswith("delegate/"), name)

    def test_contains_exactly_one_skill_md(self) -> None:
        with zipfile.ZipFile(self.package_real()) as zf:
            names = zf.namelist()
        self.assertEqual(["delegate/SKILL.md"], [n for n in names if n.endswith("SKILL.md")])

    def test_evals_and_fixtures_are_excluded(self) -> None:
        with zipfile.ZipFile(self.package_real()) as zf:
            names = zf.namelist()
        self.assertEqual([], [n for n in names if "evals" in n])

    def test_archive_is_byte_reproducible(self) -> None:
        first = self.package_real().read_bytes()
        second = package_skill.package(
            REPO_ROOT / "delegate", self.tmp / "dist2", quiet=True
        ).read_bytes()
        self.assertEqual(first, second)

    def test_junk_files_are_excluded(self) -> None:
        skill = write_skill(self.tmp, dirname="delegate")
        (skill / ".DS_Store").write_text("junk", encoding="utf-8")
        (skill / "notes.pyc").write_text("junk", encoding="utf-8")
        cache = skill / "__pycache__"
        cache.mkdir()
        (cache / "x.py").write_text("junk", encoding="utf-8")
        archive = package_skill.package(skill, self.tmp / "out", quiet=True)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertEqual(["delegate/SKILL.md"], names)

    def test_packaging_fails_on_invalid_skill(self) -> None:
        skill = write_skill(self.tmp, frontmatter="name: delegate")  # no description
        # package() renders the failing report; keep it out of the test log.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ValueError):
                package_skill.package(skill, self.tmp / "out", quiet=True)

    def test_packaging_fails_without_skill_md(self) -> None:
        empty = self.tmp / "delegate"
        empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            package_skill.package(empty, self.tmp / "out", quiet=True)

    def test_verify_rejects_extra_skill_md(self) -> None:
        archive = self.tmp / "bad.skill"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("delegate/SKILL.md", "---\nname: delegate\n---\n# x\n")
            zf.writestr("delegate/nested/SKILL.md", "---\nname: nested\n---\n# y\n")
        with self.assertRaises(ValueError):
            package_skill.verify(archive, "delegate")

    def test_verify_rejects_wrong_root(self) -> None:
        archive = self.tmp / "bad.skill"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("SKILL.md", "---\nname: delegate\n---\n# x\n")
        with self.assertRaises(ValueError):
            package_skill.verify(archive, "delegate")


class TestExclusionRules(unittest.TestCase):
    """should_exclude() works on arcnames rooted at the skill folder name."""

    def test_skill_md_is_kept(self) -> None:
        self.assertFalse(package_skill.should_exclude(Path("delegate/SKILL.md")))

    def test_root_evals_are_excluded(self) -> None:
        self.assertTrue(package_skill.should_exclude(Path("delegate/evals/evals.json")))

    def test_nested_evals_directory_is_kept(self) -> None:
        """Only evals at the skill root is a development aid."""
        self.assertFalse(
            package_skill.should_exclude(Path("delegate/references/evals/notes.md"))
        )

    def test_pycache_is_excluded_at_any_depth(self) -> None:
        self.assertTrue(
            package_skill.should_exclude(Path("delegate/references/__pycache__/x.py"))
        )

    def test_glob_and_name_exclusions(self) -> None:
        self.assertTrue(package_skill.should_exclude(Path("delegate/x.pyc")))
        self.assertTrue(package_skill.should_exclude(Path("delegate/.DS_Store")))


class TestPackagerVerify(TempRepoTest):
    def write_archive(self, *entries: tuple[str, str]) -> Path:
        archive = self.tmp / "candidate.skill"
        with zipfile.ZipFile(archive, "w") as zf:
            for name, content in entries:
                zf.writestr(name, content)
        return archive

    def test_accepts_a_well_formed_archive(self) -> None:
        archive = self.write_archive(
            ("delegate/SKILL.md", "---\nname: delegate\n---\n# x\n"),
            ("delegate/references/notes.md", "hi"),
        )
        package_skill.verify(archive, "delegate")  # must not raise

    def test_rejects_missing_skill_md(self) -> None:
        archive = self.write_archive(("delegate/references/notes.md", "hi"))
        with self.assertRaisesRegex(ValueError, "missing delegate/SKILL.md"):
            package_skill.verify(archive, "delegate")

    def test_rejects_packaged_evals(self) -> None:
        archive = self.write_archive(
            ("delegate/SKILL.md", "---\nname: delegate\n---\n# x\n"),
            ("delegate/evals/evals.json", "{}"),
        )
        with self.assertRaisesRegex(ValueError, "evals must not be packaged"):
            package_skill.verify(archive, "delegate")

    def test_rejects_corrupt_entry(self) -> None:
        archive = self.write_archive(("delegate/SKILL.md", "---\nname: delegate\n---\n# x\n"))
        # Flip the last byte of the compressed payload so the CRC no longer
        # matches and testzip() reports the entry.
        data = bytearray(archive.read_bytes())
        offset = data.rindex(b"# x")
        data[offset] = data[offset] ^ 0xFF
        archive.write_bytes(bytes(data))
        with self.assertRaisesRegex(ValueError, "corrupt entry"):
            package_skill.verify(archive, "delegate")


class TestPackagerOutput(TempRepoTest):
    def test_non_quiet_run_lists_added_and_skipped_entries(self) -> None:
        skill = write_skill(self.tmp, dirname="delegate")
        (skill / ".DS_Store").write_text("junk", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            package_skill.package(skill, self.tmp / "out")
        printed = out.getvalue()
        self.assertIn("added:   delegate/SKILL.md", printed)
        self.assertIn("skipped: delegate/.DS_Store", printed)


class CliTest(TempRepoTest):
    """Runs a module's main() against a throwaway repo root."""

    def setUp(self) -> None:
        super().setUp()
        # Both modules resolve arguments against validate_skill.REPO_ROOT, and
        # the validator uses it for relative labels and the leak scan.
        for module in (validate_skill, package_skill):
            patcher = mock.patch.object(module, "REPO_ROOT", self.tmp)
            patcher.start()
            self.addCleanup(patcher.stop)

    def copy_real_skill(self) -> Path:
        target = self.tmp / "delegate"
        shutil.copytree(REPO_ROOT / "delegate", target)
        return target

    def run_main(self, main, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()


class TestValidatorCli(CliTest):
    def test_valid_skill_exits_zero(self) -> None:
        self.copy_real_skill()
        code, out, _ = self.run_main(validate_skill.main, [])
        self.assertEqual(0, code, out)
        self.assertIn("OK — delegate validated (local rules)", out)

    def test_for_upload_flag_is_reported_in_the_summary(self) -> None:
        self.copy_real_skill()
        code, out, _ = self.run_main(validate_skill.main, ["--for-upload"])
        self.assertEqual(0, code, out)
        self.assertIn("upload rules", out)

    def test_explicit_skill_argument(self) -> None:
        write_skill(self.tmp, name="other", dirname="other")
        code, out, _ = self.run_main(validate_skill.main, ["other"])
        self.assertEqual(0, code, out)
        self.assertIn("OK — other validated", out)

    def test_invalid_skill_exits_one_and_reports_to_stderr(self) -> None:
        write_skill(self.tmp, frontmatter="name: delegate")  # no description
        code, _, err = self.run_main(validate_skill.main, [])
        self.assertEqual(1, code)
        self.assertIn("missing required key 'description'", err)
        self.assertIn("error(s)", err)

    def test_warnings_do_not_fail_the_run(self) -> None:
        write_skill(self.tmp, body="prose without a heading\n")
        code, out, _ = self.run_main(validate_skill.main, [])
        self.assertEqual(0, code, out)
        self.assertIn("warning:", out)
        self.assertIn("no top-level", out)

    def test_missing_directory_exits_two(self) -> None:
        code, _, err = self.run_main(validate_skill.main, ["nope"])
        self.assertEqual(2, code)
        self.assertIn("is not a directory", err)


class TestPackagerCli(CliTest):
    def test_packages_into_dist_by_default(self) -> None:
        self.copy_real_skill()
        code, out, _ = self.run_main(package_skill.main, ["--quiet"])
        self.assertEqual(0, code, out)
        self.assertTrue((self.tmp / "dist" / "delegate.skill").is_file())
        self.assertIn("packaged dist/delegate.skill", out)

    def test_quiet_suppresses_per_entry_output(self) -> None:
        self.copy_real_skill()
        _, quiet_out, _ = self.run_main(package_skill.main, ["--quiet"])
        self.assertNotIn("added:", quiet_out)
        _, loud_out, _ = self.run_main(package_skill.main, [])
        self.assertIn("added:", loud_out)

    def test_custom_out_dir(self) -> None:
        self.copy_real_skill()
        code, out, _ = self.run_main(package_skill.main, ["delegate", "--out-dir", "build"])
        self.assertEqual(0, code, out)
        self.assertTrue((self.tmp / "build" / "delegate.skill").is_file())

    def test_invalid_skill_exits_one(self) -> None:
        write_skill(self.tmp, frontmatter="name: delegate")  # no description
        code, _, err = self.run_main(package_skill.main, ["--quiet"])
        self.assertEqual(1, code)
        self.assertIn("validation failed", err)
        self.assertFalse((self.tmp / "dist").exists())

    def test_directory_without_skill_md_exits_one(self) -> None:
        (self.tmp / "delegate").mkdir()
        code, _, err = self.run_main(package_skill.main, ["--quiet"])
        self.assertEqual(1, code)
        self.assertIn("SKILL.md not found", err)

    def test_missing_directory_exits_two(self) -> None:
        code, _, err = self.run_main(package_skill.main, ["nope"])
        self.assertEqual(2, code)
        self.assertIn("is not a directory", err)


class TestInstaller(TempRepoTest):
    SCRIPT = REPO_ROOT / "install.sh"

    def run_installer(self, *args: str, home: Path | None = None):
        env = dict(os.environ)
        env["HOME"] = str(home or self.tmp / "home")
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [str(self.SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.tmp),
        )

    def installed_path(self, root: Path) -> Path:
        return root / ".claude" / "skills" / "delegate" / "SKILL.md"

    def test_personal_install(self) -> None:
        home = self.tmp / "home"
        result = self.run_installer(home=home)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.installed_path(home).is_file())

    def test_reinstall_without_force_fails(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        result = self.run_installer(home=home)
        self.assertEqual(1, result.returncode)
        self.assertIn("already exists", result.stderr)

    def test_reinstall_with_force_succeeds(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        result = self.run_installer("--force", home=home)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_uninstall_removes_skill(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        result = self.run_installer("--uninstall", home=home)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.installed_path(home).exists())

    def test_uninstall_when_absent_is_not_an_error(self) -> None:
        result = self.run_installer("--uninstall", home=self.tmp / "empty-home")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_project_install_with_spaces_in_path(self) -> None:
        project = self.tmp / "my project"
        project.mkdir()
        result = self.run_installer("--project", str(project))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.installed_path(project).is_file())

    def test_project_install_defaults_to_cwd(self) -> None:
        result = self.run_installer("--project")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.installed_path(self.tmp).is_file())

    def test_evals_are_not_installed(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        installed_dir = self.installed_path(home).parent
        self.assertEqual(["SKILL.md"], [p.name for p in installed_dir.iterdir()])

    def test_unknown_argument_exits_two(self) -> None:
        result = self.run_installer("--bogus")
        self.assertEqual(2, result.returncode)

    def test_help_does_not_leak_shell_source(self) -> None:
        result = self.run_installer("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Install the delegate skill", result.stdout)
        self.assertNotIn("set -euo pipefail", result.stdout)

    def test_empty_home_is_refused_and_writes_nothing_to_root(self) -> None:
        """set -u catches an unset HOME, not an empty one.

        Without the guard, dest becomes /.claude/skills/delegate and the
        installer writes to the filesystem root.
        """
        env = dict(os.environ)
        env["HOME"] = ""
        result = subprocess.run(
            [str(self.SCRIPT)], capture_output=True, text=True, env=env, cwd=str(self.tmp)
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("filesystem root", result.stderr)
        self.assertFalse(Path("/.claude/skills/delegate").exists())

    def test_root_home_is_refused(self) -> None:
        """HOME=/ strips to '' the same way an empty HOME does."""
        env = dict(os.environ)
        env["HOME"] = "/"
        result = subprocess.run(
            [str(self.SCRIPT)], capture_output=True, text=True, env=env, cwd=str(self.tmp)
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("filesystem root", result.stderr)

    def test_empty_home_uninstall_is_refused(self) -> None:
        """The guard must run before --uninstall, which would rm -rf the root path."""
        env = dict(os.environ)
        env["HOME"] = ""
        result = subprocess.run(
            [str(self.SCRIPT), "--uninstall"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.tmp),
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("filesystem root", result.stderr)

    def test_project_path_must_exist(self) -> None:
        result = self.run_installer("--project", str(self.tmp / "no" / "such" / "dir"))
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("not a directory", result.stderr)
        self.assertFalse((self.tmp / "no").exists())

    def test_invocation_via_symlink_resolves_source(self) -> None:
        link = self.tmp / "install-link.sh"
        link.symlink_to(self.SCRIPT)
        home = self.tmp / "home"
        env = dict(os.environ)
        env["HOME"] = str(home)
        home.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(link)], capture_output=True, text=True, env=env, cwd=str(self.tmp)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.installed_path(home).is_file())

    def test_installed_skill_matches_repo_copy(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        self.assertEqual(
            (REPO_ROOT / "delegate" / "SKILL.md").read_text(encoding="utf-8"),
            self.installed_path(home).read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
