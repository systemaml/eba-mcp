import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eba_pipeline.config import confidence_threshold_env
from eba_pipeline.env import find_repo_root, load_env, parse_env, resolve_db_path


class EnvParsingTests(unittest.TestCase):
    def test_missing_env_file_leaves_environment_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _ = (root / "package.json").write_text("{}", encoding="utf-8")
            env: dict[str, str] = {}

            result = load_env(start_dir=root, environ=env)

            self.assertEqual(env, {})
            self.assertIsNone(result.env_path)
            self.assertEqual(result.loaded_keys, frozenset())

    def test_invalid_env_lines_raise_clear_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing '='"):
            _ = parse_env("EBA_DB_PATH", "fixture.env")
        with self.assertRaisesRegex(ValueError, "Invalid .env key"):
            _ = parse_env("1BAD=value", "fixture.env")

    def test_dotenv_defaults_do_not_override_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _ = (root / "package.json").write_text("{}", encoding="utf-8")
            _ = (root / ".env").write_text("EBA_DB_PATH=from-dotenv.db\nOLLAMA_URL=http://dotenv\n", encoding="utf-8")
            env = {"EBA_DB_PATH": "from-process.db"}

            result = load_env(start_dir=root, environ=env)

            self.assertEqual(env["EBA_DB_PATH"], "from-process.db")
            self.assertEqual(env["OLLAMA_URL"], "http://dotenv")
            self.assertEqual(result.loaded_keys, frozenset({"OLLAMA_URL"}))

    def test_cli_db_path_takes_precedence_over_env_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _ = (root / "package.json").write_text("{}", encoding="utf-8")
            _ = (root / ".env").write_text("EBA_DB_PATH=from-dotenv.db\n", encoding="utf-8")
            env = {"EBA_DB_PATH": "from-process.db"}

            db_path = resolve_db_path(["--db", "from-cli.db"], start_dir=root, environ=env)

            self.assertEqual(db_path, Path("from-cli.db"))

    def test_repo_root_lookup_resolves_dotenv_relative_db_from_nested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            _ = (root / "package.json").write_text("{}", encoding="utf-8")
            _ = (root / ".env").write_text("EBA_DB_PATH=data/test.db\n", encoding="utf-8")
            env: dict[str, str] = {}

            self.assertEqual(find_repo_root(nested), root)
            db_path = resolve_db_path([], start_dir=nested, environ=env)

            self.assertEqual(db_path, root / "data" / "test.db")

    def test_confidence_threshold_env_accepts_decimal_string(self) -> None:
        with patch.dict("os.environ", {"LLM_REPAIR_CONFIDENCE_THRESHOLD": "0.9"}):
            self.assertEqual(confidence_threshold_env("LLM_REPAIR_CONFIDENCE_THRESHOLD", 0.8), 0.9)

    def test_confidence_threshold_env_rejects_out_of_range_values(self) -> None:
        with patch.dict("os.environ", {"LLM_REPAIR_CONFIDENCE_THRESHOLD": "1.1"}):
            with self.assertRaisesRegex(ValueError, "less than or equal to 1"):
                _ = confidence_threshold_env("LLM_REPAIR_CONFIDENCE_THRESHOLD", 0.8)
