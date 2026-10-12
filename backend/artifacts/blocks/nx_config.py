"""Config loader (vetted block). Config files never hold secrets: keys ending in _env name an environment variable that holds the value."""
import json
import os
import re
from pathlib import Path


class ConfigError(Exception):
    pass


SECRET_KEY = re.compile(r"(password|passwd|secret|token|api_?key|dsn|connection_?string|credentials?)$", re.I)
USERINFO = re.compile(r"://[^/@\s:]+:[^/@\s]+@")


def _check(node, path="config"):
    if isinstance(node, dict):
        for k, v in node.items():
            if SECRET_KEY.search(k) and not k.lower().endswith("_env") and isinstance(v, str) and v:
                raise ConfigError(f"{path}.{k}: do not put secrets in the config file. Use an environment variable and store only its name under '{k}_env'.")
            _check(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _check(v, f"{path}[{i}]")
    elif isinstance(node, str) and USERINFO.search(node):
        raise ConfigError(f"{path}: a URL with a user name and password is not allowed in the config file. Use an environment variable and store only its name.")


def load(path=None, env="NX_CONFIG", default=None):
    p = path or os.environ.get(env) or default
    if not p or not Path(p).is_file():
        raise ConfigError("Configuration file not found. Copy config.example.json to config.json, edit it, or set the NX_CONFIG environment variable to its path.")
    try:
        cfg = json.loads(Path(p).read_text(encoding="utf-8"))
    except ValueError:
        raise ConfigError("Configuration file is not valid JSON.") from None
    if not isinstance(cfg, dict):
        raise ConfigError("Configuration file must contain a JSON object.")
    _check(cfg)
    return cfg


def secret(cfg, key):
    name = cfg.get(key)
    if not name or not isinstance(name, str):
        raise ConfigError(f"Missing '{key}' in the configuration (the name of an environment variable).")
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"The environment variable {name} is not set.")
    return val
