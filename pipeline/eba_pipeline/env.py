from __future__ import annotations

import os
import re
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

VALID_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_DB_PATH = Path("data/corpora/eba-corpus.db")


@dataclass(frozen=True)
class EnvLoadResult:
    repo_root: Path
    env_path: Path | None
    loaded_keys: frozenset[str]


def parse_env(text: str, source: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at {source}:{line_number}: missing '='")
        key, value = line.split("=", 1)
        key = key.strip()
        if not VALID_ENV_KEY.match(key):
            raise ValueError(f"Invalid .env key at {source}:{line_number}: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value).rstrip()
        values[key] = value
    return values


def find_repo_root(start_dir: Path | str) -> Path:
    current = Path(start_dir).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "package.json").exists():
            return candidate
    return current


def load_env(
    start_dir: Path | str | None = None,
    environ: MutableMapping[str, str] | None = None,
    env_file: Path | str | None = None,
) -> EnvLoadResult:
    env = environ if environ is not None else os.environ
    repo_root = find_repo_root(start_dir or Path.cwd())
    candidate = Path(env_file).resolve() if env_file is not None else repo_root / ".env"
    loaded_keys: set[str] = set()

    if candidate.exists():
        values = parse_env(candidate.read_text(encoding="utf-8"), str(candidate))
        for key, value in values.items():
            if key not in env:
                env[key] = value
                loaded_keys.add(key)

    return EnvLoadResult(
        repo_root=repo_root,
        env_path=candidate if candidate.exists() else None,
        loaded_keys=frozenset(loaded_keys),
    )


def parse_positive_int_env(name: str, default: int, environ: MutableMapping[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer number of milliseconds") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0 milliseconds")
    return value


def resolve_db_path(
    argv: Sequence[str] = (),
    start_dir: Path | str | None = None,
    environ: MutableMapping[str, str] | None = None,
    env_file: Path | str | None = None,
) -> Path:
    if "--db" in argv:
        index = list(argv).index("--db")
        try:
            return Path(argv[index + 1])
        except IndexError as exc:
            raise ValueError("--db requires a path") from exc

    env = environ if environ is not None else os.environ
    result = load_env(start_dir=start_dir, environ=env, env_file=env_file)
    configured = env.get("EBA_DB_PATH")
    if configured:
        path = Path(configured)
        if not path.is_absolute() and result.env_path is not None and "EBA_DB_PATH" in result.loaded_keys:
            return result.env_path.parent / path
        return path
    return result.repo_root / DEFAULT_DB_PATH
