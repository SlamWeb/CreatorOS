"""Real local Git installation under Windows-length paths, without network."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase, main, mock
import os
import subprocess

from creatoros.integrations import producer_skills
from creatoros.integrations.producer_skills import GitSkillInstaller, ProducerSkillCatalog, SkillInstallService, github_url


class GitSkillInstallerLongPathTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="creatoros-git-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "source-repository"
        self.repository.mkdir()
        (self.repository / "assets").mkdir()
        (self.repository / "assets" / "reference.txt").write_bytes(b"committed reference asset\n")
        (self.repository / "SKILL.md").write_text(
            "---\nname: local-test\ndescription: isolated test\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n# Local example\n", encoding="utf-8")
        # These per-process options keep both the source setup and the installer
        # off the network and independent from the user's Git configuration.
        self.git_env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": f"url.{self.repository.as_uri()}.insteadOf",
            "GIT_CONFIG_VALUE_0": "https://github.com/local/example", "GIT_CONFIG_KEY_1": "core.longpaths",
            "GIT_CONFIG_VALUE_1": "false", "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0"}
        self.environment = mock.patch.dict(os.environ, self.git_env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        self.git("branch", "-M", "fixture-ref")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "release-1")
        self.git("checkout", "-b", "different-ref")
        (self.repository / "assets" / "reference.txt").write_bytes(b"different branch asset\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "different branch")
        self.other_commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "fixture-ref")
        self.git("checkout", "-b", "nested-ref")
        nested = self.repository / "packs" / "teaching"
        nested.mkdir(parents=True)
        (self.repository / "SKILL.md").rename(nested / "SKILL.md")
        (self.repository / "assets").rename(nested / "assets")
        self.git("add", "-A")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "nested skill")
        self.nested_commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "fixture-ref")

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(self.repository), *args],
                              check=True, capture_output=True, encoding="utf-8", errors="replace")

    def deep_directory(self, name, length=233):
        path = self.root / name
        # The reported workspace is 233 characters; install's /source makes
        # the actual checkout root exactly 240 characters.
        while len(str(path)) < length:
            path /= "d" * min(45, length - 1 - len(str(path)))
        path.mkdir(parents=True)
        self.assertEqual(len(str(path)), length)
        return path

    def test_installs_deep_directory_via_clone_and_ref_fetch(self):
        installer = GitSkillInstaller()
        for name, suffix in (("default", ""), ("ref", "/tree/fixture-ref"),
                             ("tag", "/tree/release-1"), ("sha", "/tree/" + self.commit)):
            with self.subTest(suffix=suffix):
                directory = self.deep_directory(name)
                receipt = installer.install("https://github.com/local/example" + suffix, directory, Event())
                self.assertEqual(receipt.skill_path, ".")
                self.assertTrue(receipt.carousel_compatible)
                self.assertIn("Local example", (directory / "source" / "SKILL.md").read_text(encoding="utf-8"))
                # Catalogue registration must archive the actual commit, not
                # uncommitted working-tree changes or an installer claim.
                (directory / "source" / "assets" / "reference.txt").write_bytes(b"uncommitted alteration\n")
                # Remove only the test URL rewrite: the checkout retains the
                # original HTTPS remote, which the real catalog must verify.
                with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                                                  "GIT_CONFIG_VALUE_0": "false"}):
                    catalog = ProducerSkillCatalog(self.root / ("catalog-" + name))
                    record = catalog.register(directory, "https://github.com/local/example" + suffix, receipt)
                self.assertEqual(record["name"], "local-test")
                self.assertEqual(record["commit"], self.commit)
                self.assertTrue((Path(record["local_path"]) / "SKILL.md").is_file())
                self.assertEqual((Path(record["local_path"]) / "assets" / "reference.txt").read_bytes(),
                                 b"committed reference asset\n")

    def test_catalog_rejects_ref_other_than_fetched_commit(self):
        directory = self.deep_directory("wrong-ref")
        receipt = GitSkillInstaller().install("https://github.com/local/example/tree/fixture-ref", directory, Event())
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                                          "GIT_CONFIG_VALUE_0": "false"}):
            catalog = ProducerSkillCatalog(self.root / "wrong-catalog")
            with self.assertRaisesRegex(ValueError, "下载的仓库与请求不一致"):
                catalog.register(directory, "https://github.com/other/example/tree/fixture-ref", receipt)
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                catalog.register(directory, "https://github.com/local/example/tree/different-ref", receipt)

    def test_branch_and_skill_subdirectory_are_verified_together(self):
        directory = self.deep_directory("nested-skill")
        url = "https://github.com/local/example/tree/nested-ref/packs/teaching"
        receipt = GitSkillInstaller().install(url, directory, Event())
        self.assertEqual(receipt.skill_path, "packs/teaching")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                                          "GIT_CONFIG_VALUE_0": "false"}):
            catalog = ProducerSkillCatalog(self.root / "nested-catalog")
            with self.assertRaisesRegex(ValueError, "安装回执与指定 Git ref/Skill 路径不一致"):
                catalog.register(directory, "https://github.com/local/example/tree/nested-ref/packs/wrong", receipt)
            record = catalog.register(directory, url, receipt)
        self.assertEqual(record["commit"], self.nested_commit)
        self.assertEqual(record["skill_path"], "packs/teaching")
        self.assertEqual((Path(record["local_path"]) / "assets" / "reference.txt").read_bytes(),
                         b"committed reference asset\n")

    def test_catalog_rejects_a_tampered_fetched_ref(self):
        directory = self.deep_directory("tampered-ref")
        receipt = GitSkillInstaller().install("https://github.com/local/example/tree/fixture-ref", directory, Event())
        source = directory / "source"
        for args in (("fetch", "origin", "different-ref"),
                     ("update-ref", "refs/remotes/origin/fixture-ref", self.other_commit)):
            subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(source), *args],
                           check=True, capture_output=True)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                                          "GIT_CONFIG_VALUE_0": "false"}):
            with self.assertRaisesRegex(ValueError, "安装回执与指定 Git ref"):
                ProducerSkillCatalog(self.root / "tampered-catalog").register(
                    directory, "https://github.com/local/example/tree/fixture-ref", receipt)

    def test_real_install_queue_publishes_committed_assets_at_reported_length(self):
        # Service workspace = catalog_root/work/<sha256>/attempt-1. The local
        # transport only rewrites network access during actual Git operations.
        suffix = Path("work") / ("0" * 64) / "attempt-1"
        catalog_root = self.deep_directory("service-catalog", 233 - len(str(suffix)) - 1)
        owner = self
        class OfflineTransport(GitSkillInstaller):
            def _git(self, args, cwd=None):
                with mock.patch.dict(os.environ, owner.git_env):
                    return super()._git(args, cwd)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                                          "GIT_CONFIG_VALUE_0": "false"}):
            service = SkillInstallService(ProducerSkillCatalog(catalog_root), installer=OfflineTransport())
            try:
                for suffix in ("", "/tree/fixture-ref"):
                    job = service.submit("https://github.com/local/example" + suffix)
                    service.thread.join(40)
                    completed = service.get(job["id"])
                    self.assertEqual(completed["status"], "installed", completed)
                    source = catalog_root / "work" / job["id"] / "attempt-1" / "source"
                    self.assertEqual(len(str(source)), 240)
                    skill = completed["skill"]
                    self.assertEqual(skill["commit"], self.commit)
                    self.assertEqual((Path(skill["local_path"]) / "assets" / "reference.txt").read_bytes(),
                                     b"committed reference asset\n")
            finally:
                service.shutdown()

    def test_git_failure_preserves_underlying_error(self):
        directory = self.deep_directory("missing-ref")
        with self.assertRaises(ValueError) as raised:
            GitSkillInstaller().install("https://github.com/local/example/tree/no-such-ref-creatoros", directory, Event())
        self.assertIn("no-such-ref-creatoros", str(raised.exception))

    def test_ref_cannot_be_interpreted_as_a_git_option(self):
        url = "https://github.com/local/example/tree/-x"
        with self.assertRaisesRegex(ValueError, "ref.*-"):
            github_url(url)
        with mock.patch.object(GitSkillInstaller, "_git") as invoke:
            with self.assertRaisesRegex(ValueError, "ref.*-"):
                GitSkillInstaller().install(url, self.deep_directory("option-ref"), Event())
        invoke.assert_not_called()

    def test_invalid_git_ref_reports_the_real_git_error(self):
        directory = self.deep_directory("invalid-ref")
        with self.assertRaises(ValueError) as raised:
            GitSkillInstaller().install("https://github.com/local/example/tree/bad..ref", directory, Event())
        self.assertIn("bad..ref", str(raised.exception))

    def test_existing_smoke_git_fixture_works_at_reported_checkout_length(self):
        from tests.smoke_producer_skills import fixture
        directory = self.deep_directory("smoke-fixture")
        receipt = fixture(directory)
        self.assertEqual(receipt.skill_path, ".")
        self.assertTrue((directory / "source" / "SKILL.md").is_file())
        result = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(directory / "source"),
                                 "rev-parse", "--verify", "HEAD"], check=True, capture_output=True)
        self.assertTrue(result.stdout.strip())

    def test_non_windows_git_command_remains_unmodified(self):
        with mock.patch.object(producer_skills, "os", SimpleNamespace(name="posix")), \
             mock.patch.object(producer_skills.subprocess, "run") as run:
            GitSkillInstaller()._git(["--version"])
        self.assertEqual(run.call_args.args[0], ["git", "--version"])

    def test_windows_option_only_applies_to_this_invocation(self):
        with mock.patch.object(producer_skills, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(producer_skills.subprocess, "run") as run:
            GitSkillInstaller()._git(["--version"])
        self.assertEqual(run.call_args.args[0], ["git", "-c", "core.longpaths=true", "--version"])


if __name__ == "__main__":
    main()
