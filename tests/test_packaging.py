"""End-to-end contract tests for the GoogleTasks Pake package.

Encodes the web2app best-practice rules as executable assertions so a future
edit to app.json / release.yml / AeroSpace config cannot silently regress them.

Run:
    python3 -m unittest discover -s tests -v

Env overrides (for regression testing against mutated copies):
    GT_AEROSPACE_TOML   default ~/.config/aerospace/aerospace.toml
    GT_STICKY_SCRIPT    default ~/.config/aerospace/scripts/sticky-notes.sh
    GT_LIVE=1           also run the workspace-switching follow check
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_JSON = REPO / "app.json"
WORKFLOW = REPO / ".github/workflows/release.yml"
AEROSPACE_TOML = Path(
    os.environ.get("GT_AEROSPACE_TOML", "~/.config/aerospace/aerospace.toml")
).expanduser()
STICKY_SCRIPT = Path(
    os.environ.get("GT_STICKY_SCRIPT", "~/.config/aerospace/scripts/sticky-notes.sh")
).expanduser()
INSTALLED_APP = Path("/Applications/GoogleTasks.app")

# Property names accepted by pake-cli 3.17.2 (schema/pake.schema.json).
# Frozen here so an unknown/typo'd key fails fast locally instead of in CI.
PAKE_SCHEMA_KEYS = {
    "$schema", "url", "name", "identifier", "title", "icon", "width", "height",
    "useLocalFile", "fullscreen", "hideTitleBar", "hideWindowDecorations",
    "multiArch", "inject", "debug", "downloadDir", "proxyUrl", "basicAuth",
    "userAgent", "targets", "windowsToolchain", "appVersion", "alwaysOnTop",
    "maximize", "darkMode", "disabledWebShortcuts", "activationShortcut",
    "showSystemTray", "systemTrayIcon", "hideOnClose", "incognito", "wasm",
    "enableDragDrop", "keepBinary", "bundle", "multiInstance", "multiWindow",
    "startToTray", "forceInternalNavigation", "internalUrlRegex", "safeDomain",
    "enableFind", "installerLanguage", "zoom", "minWidth", "minHeight",
    "ignoreCertificateErrors", "iterativeBuild", "newWindow", "install",
    "camera", "microphone",
}

# Google Tasks has no mobile web UI (verified: iPhone UA returns byte-identical
# desktop HTML; /tasks/u/0/ is 404), so the desktop viewport is correct here.
DESKTOP_VIEWPORT = (1200, 800)
DESKTOP_MIN = (800, 600)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def pake_identifier(url, name):
    """Mirror pake-cli getIdentifier(): md5("<url>::<name>")[:6]."""
    return "com.pake.a" + hashlib.md5(f"{url}::{name}".encode()).hexdigest()[:6]


def safe_domain_regex(domains):
    """Mirror pake-cli safeDomainsToRegex(): host + subdomains, not path text."""
    escaped = [
        re.escape(part.strip().lower()) for part in domains.split(",") if part.strip()
    ]
    if not escaped:
        return ""
    return (
        r"^https?:\/\/(?:[^/?#@]+\.)*(?:%s)(?::\d+)?(?:[/?#]|$)" % "|".join(escaped)
    )


def aerospace_binary():
    return shutil.which("aerospace") or "/opt/homebrew/bin/aerospace"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


class AppJsonContractTest(unittest.TestCase):
    """app.json is the single declarative build input; every field is load-bearing."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_json(APP_JSON)

    def test_every_key_exists_in_pake_schema(self):
        unknown = set(self.cfg) - PAKE_SCHEMA_KEYS
        self.assertEqual(set(), unknown, f"unknown pake config keys: {unknown}")

    def test_identifier_matches_pake_derivation(self):
        # Guards against silent drift: pake hashes "<url>::<name>", so adding or
        # dropping the URL's trailing slash changes the bundle id and would
        # orphan every AeroSpace rule keyed on it.
        self.assertEqual(
            pake_identifier(self.cfg["url"], self.cfg["name"]),
            self.cfg["identifier"],
        )

    def test_identifier_is_valid_bundle_id(self):
        self.assertRegex(
            self.cfg["identifier"], r"^[a-zA-Z][a-zA-Z0-9.-]*[a-zA-Z0-9]$"
        )

    def test_desktop_viewport_and_floor(self):
        self.assertEqual(DESKTOP_VIEWPORT[0], self.cfg["width"])
        self.assertEqual(DESKTOP_VIEWPORT[1], self.cfg["height"])
        self.assertEqual(DESKTOP_MIN[0], self.cfg["minWidth"])
        self.assertEqual(DESKTOP_MIN[1], self.cfg["minHeight"])

    def test_native_title_bar_kept(self):
        # hideTitleBar injects a 20px #pake-top-dom drag layer that swallows
        # clicks on the top-left back/menu controls.
        self.assertIs(False, self.cfg["hideTitleBar"])

    def test_new_window_enabled_for_login_popups(self):
        self.assertIs(True, self.cfg["newWindow"])

    def test_login_flow_not_force_locked_inside_window(self):
        self.assertNotIn("forceInternalNavigation", self.cfg)

    def test_google_login_domains_stay_inside_app(self):
        regex = re.compile(safe_domain_regex(self.cfg["safeDomain"]))
        for url in (
            "https://tasks.google.com/",
            "https://accounts.google.com/v3/signin/identifier",
            "https://www.gstatic.com/tasks/tasks_192.png",
            "https://lh3.googleusercontent.com/a/avatar",
        ):
            self.assertTrue(regex.match(url), f"should be internal: {url}")

    def test_unrelated_domains_open_externally(self):
        regex = re.compile(safe_domain_regex(self.cfg["safeDomain"]))
        for url in (
            "https://example.com/",
            "https://google.com.evil.com/",
            "https://tasks.google.co/",
        ):
            self.assertIsNone(regex.match(url), f"should NOT be internal: {url}")

    def test_icon_url_is_reachable(self):
        request = urllib.request.Request(self.cfg["icon"], method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                self.assertLess(response.status, 400)
        except OSError as error:  # offline / blocked
            raise unittest.SkipTest(f"icon unreachable: {error}")


class ReleaseWorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_three_platform_matrix(self):
        for name in ("macos-14", "windows-latest", "ubuntu-22.04"):
            self.assertIn(name, self.text)

    def test_macos_builds_universal_binary(self):
        # Intel Macs report a universal-less dmg as damaged.
        self.assertIn("--multi-arch", self.text)
        self.assertIn("--targets universal", self.text)

    def test_macos_rust_targets_cover_both_archs(self):
        self.assertIn("aarch64-apple-darwin", self.text)
        self.assertIn("x86_64-apple-darwin", self.text)

    def test_build_is_declarative_and_machine_readable(self):
        self.assertIn("pake --config app.json", self.text)
        self.assertIn("--json", self.text)

    def test_release_collects_all_platform_formats(self):
        for pattern in ("*.dmg", "*.msi", "*.deb", "*.AppImage"):
            self.assertIn(pattern, self.text)


class AerospaceRuleTest(unittest.TestCase):
    """GoogleTasks is a desktop app: it belongs to Workspace 5, not the floating layer.

    It was originally shipped as a 420x840 mobile widget with global floating
    follow, but Google Tasks has no mobile web UI (iPhone UA returns the same
    desktop HTML), so the viewport and the window rule both moved to desktop.
    """

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_json(APP_JSON)
        cls.identifier = cls.cfg["identifier"]
        if not AEROSPACE_TOML.exists() or not STICKY_SCRIPT.exists():
            raise unittest.SkipTest("AeroSpace config not present (CI)")
        cls.toml = AEROSPACE_TOML.read_text(encoding="utf-8")
        cls.sticky = STICKY_SCRIPT.read_text(encoding="utf-8")
        cls.blocks = cls.toml.split("[[on-window-detected]]")

    def _own_blocks(self):
        return [
            b for b in self.blocks
            if self.identifier in b or "(?i)googletasks" in b
        ]

    def test_bundle_id_rule_matches_app_json(self):
        self.assertIn(f"if.app-id = '{self.identifier}'", self.toml)

    def test_fallback_name_rule_present(self):
        self.assertIn("if.app-name-regex-substring = '(?i)googletasks'", self.toml)

    def test_rules_pin_to_workspace_5(self):
        blocks = self._own_blocks()
        self.assertEqual(2, len(blocks), f"expected 2 rules, got {len(blocks)}")
        for block in blocks:
            self.assertIn("move-node-to-workspace 5", block)

    def test_rules_are_not_floating(self):
        # Floating would detach it from Workspace 5 and fight the sticky script.
        for block in self._own_blocks():
            self.assertNotIn("layout floating", block)

    def test_sticky_script_does_not_nan_a_pinned_app(self):
        # sticky-notes.sh only follows windows that are floating; a workspace-5
        # app must stay out of that list or it gets dragged onto every workspace.
        self.assertNotIn(f'"{self.identifier}"', self.sticky)


class InstalledAppTest(unittest.TestCase):
    """Post-install acceptance: universal binary, matching bundle id, no quarantine."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_json(APP_JSON)
        if not INSTALLED_APP.exists():
            raise unittest.SkipTest(f"{INSTALLED_APP} not installed")

    def test_bundle_id_matches_app_json(self):
        result = run(
            ["defaults", "read", str(INSTALLED_APP / "Contents/Info.plist"),
             "CFBundleIdentifier"]
        )
        self.assertEqual(self.cfg["identifier"], result.stdout.strip())

    def test_binary_is_universal(self):
        # Binary name is derived from the lowercased app name by pake.
        binary = next((INSTALLED_APP / "Contents/MacOS").iterdir())
        result = run(["lipo", "-info", str(binary)])
        self.assertEqual(0, result.returncode, result.stderr)
        for arch in ("x86_64", "arm64"):
            self.assertIn(arch, result.stdout)

    def test_size_stays_in_lightweight_range(self):
        megabytes = sum(
            f.stat().st_size for f in INSTALLED_APP.rglob("*") if f.is_file()
        ) / 1024 / 1024
        self.assertLess(megabytes, 40, f"{megabytes:.1f} MB is not a lightweight app")

    def test_gatekeeper_quarantine_cleared(self):
        result = run(["xattr", str(INSTALLED_APP)])
        self.assertNotIn("com.apple.quarantine", result.stdout)


@unittest.skipUnless(shutil.which("aerospace"), "aerospace not installed")
class LiveWindowTest(unittest.TestCase):
    """Read-only checks against the running window manager."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_json(APP_JSON)
        cls.aerospace = aerospace_binary()

    def _windows(self):
        result = run(
            [self.aerospace, "list-windows", "--monitor", "all", "--format",
             "%{app-bundle-id} %{window-id} %{window-layout} %{workspace}"]
        )
        return [
            line.split()
            for line in result.stdout.splitlines()
            if line.startswith(self.cfg["identifier"])
        ]

    def test_window_is_pinned_to_workspace_5(self):
        windows = self._windows()
        if not windows:
            raise unittest.SkipTest("GoogleTasks window is not open")
        self.assertEqual({"5"}, {w[3] for w in windows})

    @unittest.skipUnless(os.environ.get("GT_LIVE") == "1", "set GT_LIVE=1 to run")
    def test_window_stays_put_when_switching_away(self):
        # A pinned desktop app must NOT be dragged along with the floating
        # widgets when the user moves to another workspace.
        windows = self._windows()
        if not windows:
            raise unittest.SkipTest("GoogleTasks window is not open")
        window_id = windows[0][1]
        for workspace in ("1", "7"):
            with self.subTest(workspace=workspace):
                run([self.aerospace, "workspace", workspace])
                result = run(
                    [self.aerospace, "list-windows", "--monitor", "all",
                     "--format", "%{window-id} %{workspace}"]
                )
                current = None
                for line in result.stdout.splitlines():
                    if line.split()[:1] == [window_id]:
                        current = line.split()[1]
                        break
                self.assertEqual(
                    "5", current,
                    f"window {window_id} left workspace 5 while focusing {workspace}",
                )


@unittest.skipUnless(shutil.which("gh"), "gh not installed")
class PublishedReleaseTest(unittest.TestCase):
    def test_latest_release_has_all_platform_assets(self):
        result = run(
            ["gh", "release", "view", "--repo", "yaping-pro/googletask-desktop",
             "--json", "tagName,assets", "-q", ".tagName + \"\\n\" + (.assets[].name)"]
        )
        if result.returncode != 0:
            raise unittest.SkipTest(f"release lookup failed: {result.stderr.strip()}")
        output = result.stdout
        for extension in (".dmg", ".msi", ".deb", ".AppImage"):
            self.assertIn(extension, output)



if __name__ == "__main__":
    unittest.main()
