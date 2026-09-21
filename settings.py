"""설정 — config.yaml(임계값) + .env(키). 한 번 읽어 어디서나 같은 값을 쓴다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))


def _load_env(path: Path) -> None:
    """python-dotenv 없이도 돈다. 이미 있는 환경변수는 덮지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Settings:
    cfg: dict[str, Any]
    keys: dict[str, str] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        """'stage2.volume_ratio_min' 같은 점 경로로 읽는다."""
        cur: Any = self.cfg
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def key(self, name: str) -> str | None:
        v = self.keys.get(name) or os.environ.get(name)
        return v or None

    def has(self, name: str) -> bool:
        return bool(self.key(name))

    @property
    def allowed_chat_ids(self) -> set[int]:
        ids = set(int(x) for x in self.get("telegram.allowed_chat_ids", []) or [])
        raw = self.key("TELEGRAM_ALLOWED_CHAT_IDS") or ""
        ids |= {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}
        return ids

    @property
    def broadcast_chat_ids(self) -> list[int]:
        ids = [int(x) for x in self.get("telegram.broadcast_chat_ids", []) or []]
        return ids or sorted(self.allowed_chat_ids)[:1]


KEY_NAMES = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "COINGECKO_DEMO_KEY", "ETHERSCAN_API_KEY",
    "HELIUS_API_KEY", "BIRDEYE_API_KEY", "CRYPTOPANIC_TOKEN", "ANTHROPIC_API_KEY",
    "TOKENOMIST_API_KEY", "NANSEN_API_KEY", "ARKHAM_API_KEY",
)


@lru_cache(maxsize=1)
def load(config_path: str | None = None) -> Settings:
    _load_env(ROOT / ".env")
    path = Path(config_path) if config_path else ROOT / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys = {k: os.environ.get(k, "") for k in KEY_NAMES}
    return Settings(cfg=cfg, keys=keys)


def now_kst() -> datetime:
    return datetime.now(KST)


def fmt_kst(ts: float | datetime | None, pattern: str = "%m/%d %H:%M") -> str:
    """사양서: 모든 시간은 KST 로 표시한다."""
    if ts is None:
        return "-"
    dt = ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.astimezone(KST).strftime(pattern)
