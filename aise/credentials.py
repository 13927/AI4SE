from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    import keyring  # type: ignore
except Exception:  # pragma: no cover
    keyring = None

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


SERVICE_NAME = "aise"


@dataclass(frozen=True)
class OpenAIProfile:
    base_url: str
    model: str


def _config_dir() -> Path:
    # XDG on linux; fallback ~/.config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "aise"
    return Path.home() / ".config" / "aise"


def global_openai_profile_path() -> Path:
    return _config_dir() / "openai.yaml"


def load_global_openai_profile() -> OpenAIProfile | None:
    p = global_openai_profile_path()
    if not p.exists():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    base_url = str(raw.get("base_url") or raw.get("baseUrl") or "")
    model = str(raw.get("model") or "")
    if not base_url or not model:
        return None
    return OpenAIProfile(base_url=base_url, model=model)


def save_global_openai_profile(*, base_url: str, model: str) -> None:
    p = global_openai_profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"base_url": base_url, "model": model}, allow_unicode=True), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def _keyring_available() -> bool:
    return keyring is not None


def set_openai_api_key(*, base_url: str, api_key: str) -> None:
    if not _keyring_available():
        raise RuntimeError("keyring 不可用：请安装 keyring 并确保系统钥匙串可用。")
    # base_url 作为 profile key（允许同机器多个供应商）
    try:
        keyring.set_password(SERVICE_NAME, f"openai:{base_url}", api_key)
    except Exception as e:
        raise RuntimeError(f"keyring 写入失败：{type(e).__name__}: {e}")


def get_openai_api_key(*, base_url: str) -> str:
    if not _keyring_available():
        return ""
    try:
        v = keyring.get_password(SERVICE_NAME, f"openai:{base_url}")
        return v or ""
    except Exception:
        # 例如 NoKeyringError（fail backend）
        return ""


def clear_openai_api_key(*, base_url: str) -> None:
    if not _keyring_available():
        return
    try:
        keyring.delete_password(SERVICE_NAME, f"openai:{base_url}")
    except Exception:
        pass


def keyring_status() -> dict[str, Any]:
    if not _keyring_available():
        return {"available": False, "backend": ""}
    try:
        backend = str(getattr(keyring, "get_keyring", lambda: None)())
    except Exception:
        backend = ""
    # keyring.backends.fail.Keyring 表示没有可用后端
    ok = bool(backend) and ("keyring.backends.fail.Keyring" not in backend)
    return {"available": ok, "backend": backend}


def encrypted_store_path() -> Path:
    return _config_dir() / "credentials.enc"


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _get_passphrase_from_env() -> str:
    # 不允许交互：必须来自环境变量
    return os.environ.get("AISE_CRED_PASSPHRASE", "")


def set_openai_api_key_encrypted(*, base_url: str, api_key: str) -> None:
    """
    keyring 不可用时的后备方案：使用本地加密文件存储 api_key（必须设置 AISE_CRED_PASSPHRASE）。
    """
    passphrase = _get_passphrase_from_env()
    if not passphrase:
        raise RuntimeError("缺少 AISE_CRED_PASSPHRASE：无法写入加密凭据存储。")
    p = encrypted_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        raw = p.read_bytes()
        try:
            header, token = raw.split(b"\n", 1)
            obj = json.loads(header.decode("utf-8"))
            salt = base64.b64decode(obj["salt"])
            f = Fernet(_derive_fernet_key(passphrase, salt))
            data = json.loads(f.decrypt(token).decode("utf-8"))
        except Exception:
            data = {"version": 1, "entries": {}}
    else:
        salt = os.urandom(16)
        data = {"version": 1, "entries": {}}

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        entries = {}
        data["entries"] = entries
    entries[base_url] = api_key

    f = Fernet(_derive_fernet_key(passphrase, salt))
    token = f.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    header = json.dumps({"version": 1, "salt": base64.b64encode(salt).decode("utf-8")}).encode("utf-8")
    p.write_bytes(header + b"\n" + token)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def get_openai_api_key_encrypted(*, base_url: str) -> str:
    passphrase = _get_passphrase_from_env()
    if not passphrase:
        return ""
    p = encrypted_store_path()
    if not p.exists():
        return ""
    try:
        header, token = p.read_bytes().split(b"\n", 1)
        obj = json.loads(header.decode("utf-8"))
        salt = base64.b64decode(obj["salt"])
        f = Fernet(_derive_fernet_key(passphrase, salt))
        data = json.loads(f.decrypt(token).decode("utf-8"))
        ent = data.get("entries") if isinstance(data, dict) else {}
        if isinstance(ent, dict):
            v = ent.get(base_url)
            return str(v) if isinstance(v, str) else ""
    except Exception:
        return ""
    return ""
