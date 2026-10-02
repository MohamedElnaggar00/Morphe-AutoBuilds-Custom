import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import manage_config
from scripts import sync_config


class ManageConfigurationTests(unittest.TestCase):
    def base_data(self):
        return {
            "version": 1,
            "apps": {},
            "sources": {},
            "builds": [],
        }

    def test_parse_morphe_gitlab_deep_link(self):
        info = manage_config.parse_patch_source_url(
            "https://morphe.software/add-source?gitlab=Paresh-Maheshwari/paresh-patches"
        )
        self.assertEqual(info["provider"], "gitlab")
        self.assertEqual(info["project"], "Paresh-Maheshwari/paresh-patches")
        self.assertEqual(
            info["canonical_url"],
            "https://gitlab.com/Paresh-Maheshwari/paresh-patches",
        )

    def test_parse_github_repository_url(self):
        info = manage_config.parse_patch_source_url(
            "https://github.com/jasonwu1994/Gboard-patches"
        )
        self.assertEqual(info["provider"], "github")
        self.assertEqual(info["user"], "jasonwu1994")
        self.assertEqual(info["repo"], "Gboard-patches")

    def test_rejects_unsupported_patch_source(self):
        with self.assertRaises(SystemExit):
            manage_config.parse_patch_source_url("https://example.com/patches")

    def test_add_app_creates_gitlab_source_and_default_patch_selection(self):
        data = self.base_data()
        args = SimpleNamespace(
            app_name="__NEW_APP__",
            new_app_name="truecaller-test",
            display_name="Truecaller",
            package_name="com.truecaller",
            provider="apkmirror",
            provider_ref="truecaller/truecaller",
            provider_type="BUNDLE",
            dpi="nodpi",
            architecture="arm64-v8a",
            patch_source_url=(
                "https://morphe.software/add-source"
                "?gitlab=Paresh-Maheshwari/paresh-patches"
            ),
        )
        manage_config.add_app(data, args)

        self.assertIn("truecaller-test", data["apps"])
        self.assertIn("paresh-patches", data["sources"])
        source_entries = data["sources"]["paresh-patches"]["entries"]
        self.assertEqual(source_entries[-1]["provider"], "gitlab")
        self.assertEqual(
            source_entries[-1]["project"],
            "Paresh-Maheshwari/paresh-patches",
        )
        build = data["builds"][0]
        self.assertEqual(build["source"], "paresh-patches")
        self.assertNotIn("patches", build)

    @patch.object(
        manage_config,
        "latest_morphe_cli_jar",
    )
    @patch.object(manage_config.subprocess, "run")
    def test_fetch_default_patch_selection_parses_morphe_cli_output(self, run_mock, jar_mock):
        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / "morphe-cli.jar"
            jar.write_bytes(b"jar")
            jar_mock.return_value = jar
            run_mock.return_value = SimpleNamespace(
                returncode=0,
                stdout=(
                    "Name: Patch A\nEnabled: true\n"
                    "Name: Patch B\nEnabled: false\n"
                    "Name: Patch C\nEnabled: true\n"
                ),
                stderr="",
            )
            enabled, disabled = manage_config.fetch_default_patch_selection(
                "com.example.app",
                "https://github.com/example/patches",
            )
            self.assertEqual(enabled, ["Patch A", "Patch C"])
            self.assertEqual(disabled, ["Patch B"])
            run_mock.assert_called_once()
            command = run_mock.call_args.args[0]
            self.assertIn("--patches", command)
            self.assertIn("https://github.com/example/patches", command)
            self.assertIn("--filter-package-name", command)
            self.assertIn("com.example.app", command)

    def test_add_app_reuses_same_source(self):
        data = self.base_data()
        data["sources"]["paresh-patches"] = {
            "entries": [
                {"name": "paresh-patches"},
                {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                {
                    "provider": "gitlab",
                    "project": "Paresh-Maheshwari/paresh-patches",
                    "tag": "latest",
                },
            ]
        }
        args = SimpleNamespace(
            app_name="__NEW_APP__",
            new_app_name="truecaller-test",
            display_name="Truecaller",
            package_name="com.truecaller",
            provider="apkmirror",
            provider_ref="truecaller/truecaller",
            provider_type="BUNDLE",
            dpi="nodpi",
            architecture="arm64-v8a",
            patch_source_url="https://gitlab.com/Paresh-Maheshwari/paresh-patches",
        )
        manage_config.add_app(data, args)
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["builds"][0]["source"], "paresh-patches")

    def test_edit_patch_moves_between_enable_disable_and_reset(self):
        data = self.base_data()
        data["apps"]["youtube"] = {"display_name": "YouTube", "providers": {"apkmirror": {}}}
        data["sources"]["morphe"] = {"entries": []}
        build = {
            "app_name": "youtube",
            "source": "morphe",
            "arches": ["arm64-v8a"],
            "enabled": True,
            "patches": {"enable": ["Hide ads"], "disable": [], "options": []},
        }
        data["builds"] = [build]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patches_dir = root / "patches"
            patches_dir.mkdir()
            patch_file = patches_dir / "youtube-morphe.txt"
            patch_file.write_text("+ Hide ads\n- Custom branding\n", encoding="utf-8")
            with patch.object(manage_config, "ROOT", root):
                args = SimpleNamespace(
                    app_name="youtube",
                    source="morphe",
                    patch_action="exclude",
                    patch="youtube / morphe — Hide ads",
                )
                manage_config.edit_patches(data, args)
                self.assertEqual(build["patches"]["enable"], [])
                self.assertEqual(build["patches"]["disable"], ["Hide ads"])

                args.patch_action = "add"
                args.patch = "youtube / morphe — Custom branding"
                manage_config.edit_patches(data, args)
                self.assertIn("Custom branding", build["patches"]["enable"])
                self.assertNotIn("Custom branding", build["patches"]["disable"])

                args.patch_action = "reset"
                args.patch = "youtube / morphe — Hide ads"
                manage_config.edit_patches(data, args)
                self.assertNotIn("Hide ads", build["patches"]["enable"])
                self.assertNotIn("Hide ads", build["patches"]["disable"])

    def test_delete_app_requires_confirmation_and_removes_all_builds(self):
        data = self.base_data()
        data["apps"]["messenger"] = {"display_name": "Messenger", "providers": {"aptoide": {}}}
        data["sources"]["devanced"] = {"entries": []}
        data["sources"]["hushmessenger"] = {"entries": []}
        data["builds"] = [
            {"app_name": "messenger", "source": "devanced", "arches": ["arm64-v8a"], "enabled": True},
            {"app_name": "messenger", "source": "hushmessenger", "arches": ["arm64-v8a"], "enabled": True},
        ]
        with self.assertRaises(SystemExit):
            manage_config.delete_app(data, SimpleNamespace(app_name="messenger", confirm_delete=False))
        manage_config.delete_app(data, SimpleNamespace(app_name="messenger", confirm_delete=True))
        self.assertNotIn("messenger", data["apps"])
        self.assertEqual(data["builds"], [])

    def test_set_app_status_changes_all_build_entries(self):
        data = self.base_data()
        data["apps"]["messenger"] = {"display_name": "Messenger", "providers": {"aptoide": {}}}
        data["sources"]["devanced"] = {"entries": []}
        data["sources"]["hushmessenger"] = {"entries": []}
        data["builds"] = [
            {"app_name": "messenger", "source": "devanced", "enabled": True},
            {"app_name": "messenger", "source": "hushmessenger", "enabled": False},
        ]
        manage_config.set_app_status(data, SimpleNamespace(app_name="messenger", status="disabled"))
        self.assertEqual([b["enabled"] for b in data["builds"]], [False, False])
        manage_config.set_app_status(data, SimpleNamespace(app_name="messenger", status="enabled"))
        self.assertEqual([b["enabled"] for b in data["builds"]], [True, True])

    def test_sync_config_accepts_gitlab_source_entry(self):
        data = {
            "version": 1,
            "apps": {
                "truecaller": {
                    "display_name": "Truecaller",
                    "providers": {
                        "apkmirror": {
                            "package": "com.truecaller",
                            "type": "BUNDLE",
                            "arch": "arm64-v8a",
                            "dpi": "nodpi",
                        }
                    },
                }
            },
            "sources": {
                "paresh-patches": {
                    "entries": [
                        {"name": "paresh-patches"},
                        {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                        {
                            "provider": "gitlab",
                            "project": "Paresh-Maheshwari/paresh-patches",
                            "tag": "latest",
                        },
                    ]
                }
            },
            "builds": [
                {
                    "app_name": "truecaller",
                    "source": "paresh-patches",
                    "arches": ["arm64-v8a"],
                    "enabled": True,
                }
            ],
        }
        sync_config.validate(data)

    def test_generated_choices_can_be_refreshed_from_config_and_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patches_dir = root / "patches"
            patches_dir.mkdir()
            (patches_dir / "youtube-morphe.txt").write_text(
                "+ Hide ads\n- Custom branding\n", encoding="utf-8"
            )
            workflow = root / "manage-config.yml"
            workflow.write_text(
                "      app_name:\n"
                "        options:\n"
                "          # BEGIN GENERATED APP OPTIONS\n"
                "          - \"old\"\n"
                "          # END GENERATED APP OPTIONS\n"
                "      source:\n"
                "        options:\n"
                "          # BEGIN GENERATED SOURCE OPTIONS\n"
                "          - \"old\"\n"
                "          # END GENERATED SOURCE OPTIONS\n"
                "      patch:\n"
                "        options:\n"
                "          # BEGIN GENERATED PATCH OPTIONS\n"
                "          - \"__NONE__\"\n"
                "          # END GENERATED PATCH OPTIONS\n",
                encoding="utf-8",
            )
            manage_config.ROOT = root
            manage_config.WORKFLOW_PATH = workflow
            data = {
                "version": 1,
                "apps": {"youtube": {}},
                "sources": {"morphe": {}, "paresh-patches": {}},
                "builds": [
                    {
                        "app_name": "youtube",
                        "source": "morphe",
                        "arches": ["arm64-v8a"],
                        "enabled": True,
                    }
                ],
            }
            manage_config.refresh_workflow_choices(data)
            updated = workflow.read_text(encoding="utf-8")
            self.assertIn('"youtube"', updated)
            self.assertIn('"paresh-patches"', updated)
            self.assertIn('"youtube / morphe — Hide ads"', updated)
            self.assertIn('"youtube / morphe — Custom branding"', updated)
            self.assertNotIn('"old"', updated)


if __name__ == "__main__":
    unittest.main()
