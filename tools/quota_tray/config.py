"""設定檔與家目錄探索。

Windows 上一個實際的麻煩：Codex / Claude Code / Gemini 這些 CLI 可能裝在原生 Windows，
也可能跑在 WSL 裡。兩邊的家目錄完全不同（`C:\\Users\\你` vs `\\\\wsl.localhost\\Ubuntu\\home\\你`），
所以這裡會把兩邊都找出來，讓 provider 一起掃。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_ENV_VAR = "QUOTA_TRAY_CONFIG"


@dataclass
class CodexConfig:
    enabled: bool = True
    sessions_dir: str | None = None
    max_files_to_scan: int = 8


@dataclass
class ClaudeConfig:
    enabled: bool = True
    projects_dir: str | None = None
    session_window_hours: float = 5.0
    # 沒填預算就不顯示百分比，只顯示 token 數 —— 不編造官方額度。
    session_token_budget: int | None = None
    weekly_token_budget: int | None = None


@dataclass
class GeminiConfig:
    enabled: bool = True
    logs_root: str | None = None
    daily_request_limit: int = 1_000
    minute_request_limit: int = 60
    # Google 的每日額度以太平洋時間換日
    daily_reset_timezone: str = "America/Los_Angeles"


@dataclass
class AppConfig:
    refresh_seconds: float = 5.0
    hide_unavailable: bool = True
    # 是否連 WSL 裡的家目錄一起找
    search_wsl: bool = True
    # 額外要掃的家目錄（例如另一個帳號、或掛載的磁碟）
    extra_home_dirs: list[str] = field(default_factory=list)
    codex: CodexConfig = field(default_factory=CodexConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)

    def to_dict(self) -> dict:
        return asdict(self)


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "QuotaTray" / "config.json"
    return Path.home() / ".config" / "quota-tray" / "config.json"


def _build_section(cls, raw: object):
    """只吃認得的 key，缺的用預設值 —— 設定檔是人手改的，只填一兩個欄位是常態。"""
    section = cls()
    if not isinstance(raw, dict):
        return section
    known = {f for f in cls.__dataclass_fields__}
    for key, value in raw.items():
        if key in known:
            setattr(section, key, value)
    return section


def load_config(path: Path | None = None) -> tuple[AppConfig, str | None]:
    """讀設定檔。不存在或壞掉都回預設值，附帶錯誤訊息給呼叫端顯示。"""
    target = path or default_config_path()
    if not target.exists():
        return AppConfig(), None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return AppConfig(), f"設定檔讀取失敗，改用預設值：{exc}"

    if not isinstance(raw, dict):
        return AppConfig(), "設定檔最外層不是物件，改用預設值"

    config = AppConfig()
    for key in ("refresh_seconds", "hide_unavailable", "search_wsl", "extra_home_dirs"):
        if key in raw:
            setattr(config, key, raw[key])
    config.codex = _build_section(CodexConfig, raw.get("codex"))
    config.claude = _build_section(ClaudeConfig, raw.get("claude"))
    config.gemini = _build_section(GeminiConfig, raw.get("gemini"))
    return config, None


def write_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def wsl_home_dirs() -> list[Path]:
    """列出 WSL 各發行版裡的家目錄。非 Windows 或沒裝 WSL 就回空清單。"""
    if sys.platform != "win32":
        return []

    homes: list[Path] = []
    for unc in (r"\\wsl.localhost", r"\\wsl$"):
        root = Path(unc)
        try:
            if not root.exists():
                continue
            distros = list(root.iterdir())
        except OSError:
            # UNC 路徑在 WSL 沒啟動時會逾時或拒絕存取，直接跳過。
            continue

        for distro in distros:
            try:
                home_root = distro / "home"
                if not home_root.is_dir():
                    continue
                for user_home in home_root.iterdir():
                    if user_home.is_dir():
                        homes.append(user_home)
            except OSError:
                continue

        if homes:
            # wsl.localhost 與 wsl$ 指向同一份檔案系統，找到一個就夠。
            break

    return homes


def home_roots(config: AppConfig) -> list[Path]:
    """所有要搜尋的家目錄，依序為：本機家目錄 → 自訂 → WSL。"""
    roots: list[Path] = [Path.home()]
    for extra in config.extra_home_dirs:
        roots.append(Path(os.path.expandvars(str(extra))).expanduser())
    if config.search_wsl:
        roots.extend(wsl_home_dirs())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).rstrip("\\/").lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def resolve_dirs(configured: str | None, relative: str, roots: list[Path]) -> list[Path]:
    """把設定值展開成實際存在的目錄清單。

    有明確設定就只用它；沒有就在每個家目錄底下找 `relative`。
    """
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        return [candidate] if candidate.is_dir() else []
    return [root / relative for root in roots if (root / relative).is_dir()]
