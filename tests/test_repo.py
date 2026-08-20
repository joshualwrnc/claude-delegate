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

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import package_skill  # noqa: E402
import skill_common  # noqa: E402
import validate_skill  # noqa: E402
from validate_skill import validate  # noqa: E402

REAL_SKILL = REPO_ROOT / "delegate"

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

    def write_fixture(self, relative: str, text: str = "hi") -> Path:
        """Create a file under the temp repo root, parents included."""
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def assertErrorMatching(self, report, needle: str) -> None:
        joined = "\n".join(report.errors)
        self.assertIn(
            needle, joined, f"expected an error containing {needle!r}, got: {joined or '(none)'}"
        )


class TestRealRepo(TempRepoTest):
    """The shipped skill must pass under both rule sets."""

    def test_real_skill_passes_local_rules(self) -> None:
        report = validate(REAL_SKILL)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")

    def test_real_skill_passes_upload_rules(self) -> None:
        report = validate(REAL_SKILL, for_upload=True)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")

    def test_real_skill_has_no_warnings(self) -> None:
        report = validate(REAL_SKILL)
        self.assertEqual([], report.warnings)

    def test_fixture_shared_never_rules_are_exactly_two(self) -> None:
        """Eval case 3's ground truth. If this drifts, the eval is wrong."""
        brands = REAL_SKILL / "evals" / "fixtures" / "brands"
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
        first = validate(REAL_SKILL)
        second = validate(REAL_SKILL)
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
    def report_for_prompt(self, prompt: str):
        """Validate a skill whose single eval carries prompt."""
        skill = write_skill(self.tmp)
        write_evals(skill, minimal_evals(prompt=prompt))
        return self.run_validate(skill)

    def report_for_files(self, files: object):
        """Validate a skill whose single eval carries a 'files' value."""
        skill = write_skill(self.tmp)
        payload = minimal_evals()
        payload["evals"][0]["files"] = files
        write_evals(skill, payload)
        return self.run_validate(skill)

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
        report = self.report_for_prompt("read delegate/evals/fixtures/nope.md please")
        self.assertErrorMatching(report, "missing path")

    def test_existing_fixture_path_passes(self) -> None:
        self.write_fixture("delegate/evals/fixtures/real.md")
        report = self.report_for_prompt("read delegate/evals/fixtures/real.md please")
        self.assertTrue(report.ok, report.errors)

    def test_trailing_punctuation_is_stripped_from_path(self) -> None:
        self.write_fixture("delegate/evals/fixtures/real.md")
        report = self.report_for_prompt("see delegate/evals/fixtures/real.md.")
        self.assertTrue(report.ok, report.errors)

    def test_directory_reference_passes(self) -> None:
        (self.tmp / "delegate" / "evals" / "fixtures" / "dir").mkdir(parents=True)
        report = self.report_for_prompt("sweep delegate/evals/fixtures/dir/")
        self.assertTrue(report.ok, report.errors)

    def test_url_containing_repo_name_is_not_a_path_reference(self) -> None:
        """github.com/user/claude-delegate/issues/5 is not 'delegate/issues/5'."""
        report = self.report_for_prompt("see https://github.com/someone/claude-delegate/issues/5")
        self.assertTrue(report.ok, report.errors)

    def test_nested_path_ending_in_skill_dir_is_not_a_path_reference(self) -> None:
        """out/delegate/summary.md must not be read as delegate/summary.md."""
        report = self.report_for_prompt("write results to out/delegate/summary.md")
        self.assertTrue(report.ok, report.errors)

    def test_dotted_prefix_is_not_a_path_reference(self) -> None:
        report = self.report_for_prompt("module pkg.tests/helper.py is unrelated")
        self.assertTrue(report.ok, report.errors)

    def test_real_path_after_punctuation_is_still_checked(self) -> None:
        """The lookbehind must not make the check miss genuine references."""
        report = self.report_for_prompt("(delegate/evals/fixtures/gone.md)")
        self.assertErrorMatching(report, "missing path")

    def test_files_list_paths_are_checked(self) -> None:
        report = self.report_for_files(["delegate/evals/fixtures/gone.md"])
        self.assertErrorMatching(report, "missing path")

    def test_files_list_with_existing_path_passes(self) -> None:
        self.write_fixture("delegate/evals/fixtures/real.md")
        report = self.report_for_files(["delegate/evals/fixtures/real.md"])
        self.assertTrue(report.ok, report.errors)

    def test_files_must_be_a_list(self) -> None:
        self.assertErrorMatching(self.report_for_files("not-a-list"), "'files' must be a list")

    def test_files_entries_must_be_strings(self) -> None:
        self.assertErrorMatching(self.report_for_files([42]), "must be strings")


class TestLeakScan(TempRepoTest):
    def _scan(self) -> validate_skill.Report:
        report = validate_skill.Report()
        validate_skill.check_no_leaked_paths(report, self.tmp)
        return report

    def test_detects_linux_home_path(self) -> None:
        self.write_fixture("notes.md", "see /home/someone/work/file.md")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_detects_macos_home_path(self) -> None:
        self.write_fixture("notes.md", "see /Users/someone/work/file.md")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_exempt_token_suppresses(self) -> None:
        self.write_fixture("notes.md", "LEAK-CHECK-EXEMPT\nsee /home/someone/x.md")
        self.assertTrue(self._scan().ok)

    def test_ignores_binary_and_unknown_suffixes(self) -> None:
        (self.tmp / "blob.bin").write_bytes(b"/home/someone/secret")
        self.assertTrue(self._scan().ok)

    def test_ignores_dist_directory(self) -> None:
        self.write_fixture("dist/notes.md", "/home/someone/x")
        self.assertTrue(self._scan().ok)

    def test_clean_tree_passes(self) -> None:
        self.write_fixture("notes.md", "relative/path/is/fine.md")
        self.assertTrue(self._scan().ok)

    def test_detects_home_path_without_trailing_slash(self) -> None:
        """A bare /home/<name> at end of line is the same leak."""
        self.write_fixture("notes.md", "export BASE=/home/someone")
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_detects_macos_home_path_without_trailing_slash(self) -> None:
        self.write_fixture("notes.json", '{"home": "/Users/someone"}')
        self.assertErrorMatching(self._scan(), "absolute local path")

    def test_validator_source_does_not_exempt_itself(self) -> None:
        """The exemption token must not appear contiguously in the validator.

        It defines the token, so a naive literal would make the validator skip
        its own source and never report a leak in it.
        """
        source = (REPO_ROOT / "scripts" / "validate_skill.py").read_text(encoding="utf-8")
        self.assertNotIn(validate_skill.LEAK_EXEMPT_TOKEN, source)


class TestPackager(TempRepoTest):
    def package_real(self, out_name: str = "dist") -> Path:
        return package_skill.package(REAL_SKILL, self.tmp / out_name, quiet=True)

    def archive_names(self, archive: Path | None = None) -> list[str]:
        with zipfile.ZipFile(archive or self.package_real()) as zf:
            return zf.namelist()

    def test_produces_skill_archive(self) -> None:
        archive = self.package_real()
        self.assertTrue(archive.is_file())
        self.assertEqual("delegate.skill", archive.name)

    def test_entries_rooted_at_skill_folder(self) -> None:
        names = self.archive_names()
        self.assertTrue(names)
        for name in names:
            self.assertTrue(name.startswith("delegate/"), name)

    def test_contains_exactly_one_skill_md(self) -> None:
        names = self.archive_names()
        self.assertEqual(["delegate/SKILL.md"], [n for n in names if n.endswith("SKILL.md")])

    def test_evals_and_fixtures_are_excluded(self) -> None:
        self.assertEqual([], [n for n in self.archive_names() if "evals" in n])

    def test_archive_is_byte_reproducible(self) -> None:
        first = self.package_real().read_bytes()
        second = self.package_real("dist2").read_bytes()
        self.assertEqual(first, second)

    def test_junk_files_are_excluded(self) -> None:
        skill = write_skill(self.tmp, dirname="delegate")
        (skill / ".DS_Store").write_text("junk", encoding="utf-8")
        (skill / "notes.pyc").write_text("junk", encoding="utf-8")
        cache = skill / "__pycache__"
        cache.mkdir()
        (cache / "x.py").write_text("junk", encoding="utf-8")
        archive = package_skill.package(skill, self.tmp / "out", quiet=True)
        self.assertEqual(["delegate/SKILL.md"], self.archive_names(archive))

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


class TestSharedHelpers(TempRepoTest):
    """The validator and packager must agree on one set of rules."""

    def test_packager_exclusions_match_validator(self) -> None:
        excluded = [
            "delegate/evals/evals.json",
            "delegate/evals/fixtures/skills/toy/SKILL.md",
            "delegate/__pycache__/x.py",
            "delegate/references/node_modules/pkg/index.js",
            "delegate/notes.pyc",
            "delegate/.DS_Store",
        ]
        included = ["delegate/SKILL.md", "delegate/references/deep/guide.md"]
        for arcname in excluded:
            self.assertTrue(package_skill.should_exclude(Path(arcname)), arcname)
            self.assertFalse(skill_common.is_packaged(Path(*Path(arcname).parts[1:])), arcname)
        for arcname in included:
            self.assertFalse(package_skill.should_exclude(Path(arcname)), arcname)
            self.assertTrue(skill_common.is_packaged(Path(*Path(arcname).parts[1:])), arcname)

    def test_nested_evals_directory_is_still_packaged(self) -> None:
        """Only evals at the skill root is excluded."""
        self.assertTrue(skill_common.is_packaged(Path("references/evals/notes.md")))

    def test_rel_to_repo_shortens_paths_inside_the_repo(self) -> None:
        self.assertEqual(
            Path("delegate/SKILL.md"), skill_common.rel_to_repo(REAL_SKILL / "SKILL.md")
        )

    def test_rel_to_repo_passes_outside_paths_through(self) -> None:
        outside = self.tmp / "delegate" / "SKILL.md"
        self.assertEqual(outside, skill_common.rel_to_repo(outside))

    def test_resolve_skill_dir_rejects_non_directory(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(skill_common.resolve_skill_dir("no-such-skill"))
        self.assertIn("is not a directory", err.getvalue())

    def test_resolve_skill_dir_returns_absolute_path(self) -> None:
        self.assertEqual(REAL_SKILL, skill_common.resolve_skill_dir("delegate"))

    def test_both_clis_exit_two_on_a_missing_skill(self) -> None:
        for main in (validate_skill.main, package_skill.main):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(["no-such-skill"]), main.__module__)


class TestInstaller(TempRepoTest):
    SCRIPT = REPO_ROOT / "install.sh"

    def run_installer(
        self,
        *args: str,
        home: Path | None = None,
        raw_home: str | None = None,
        script: Path | None = None,
    ):
        """Run the installer with a controlled HOME.

        `raw_home` sets HOME verbatim (and creates nothing) for the cases that
        probe an empty or root HOME; otherwise the home directory is created.
        """
        env = dict(os.environ)
        if raw_home is None:
            home = Path(home or self.tmp / "home")
            home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(home)
        else:
            env["HOME"] = raw_home
        return subprocess.run(
            [str(script or self.SCRIPT), *args],
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
        result = self.run_installer(raw_home="")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("filesystem root", result.stderr)
        self.assertFalse(Path("/.claude/skills/delegate").exists())

    def test_root_home_is_refused(self) -> None:
        """HOME=/ strips to '' the same way an empty HOME does."""
        result = self.run_installer(raw_home="/")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("filesystem root", result.stderr)

    def test_empty_home_uninstall_is_refused(self) -> None:
        """The guard must run before --uninstall, which would rm -rf the root path."""
        result = self.run_installer("--uninstall", raw_home="")
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
        result = self.run_installer(home=home, script=link)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.installed_path(home).is_file())

    def test_installed_skill_matches_repo_copy(self) -> None:
        home = self.tmp / "home"
        self.run_installer(home=home)
        self.assertEqual(
            (REAL_SKILL / "SKILL.md").read_text(encoding="utf-8"),
            self.installed_path(home).read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
