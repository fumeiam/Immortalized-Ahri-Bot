import os, json, asyncio, time, pathlib
from typing import Dict, Any
from .config import DATA_DIR

_locks: dict[int, asyncio.Lock] = {}

def _path(guild_id: int) -> pathlib.Path:
    return DATA_DIR / f"{guild_id}.json"

def _default(guild_id: int) -> Dict[str, Any]:
    return {
        "guild_id": guild_id,
        "activated": False,
        "admins": [],
        "settings": {
            "automod": {"enabled": False, "banned_words": [], "spam_threshold": 5},
            "reaction_roles": {"panels": []},
            "logging": {"channel_id": None},
        },
        "last_updated": None,
    }

async def ensure_guild(guild_id: int) -> Dict[str, Any]:
    p = _path(guild_id)
    if not p.exists():
        data = _default(guild_id)
        await save_guild(guild_id, data)
        return data
    return await load_guild(guild_id)

async def load_guild(guild_id: int) -> Dict[str, Any]:
    p = _path(guild_id)
    if not p.exists():
        return await ensure_guild(guild_id)
    loop = asyncio.get_running_loop()
    def _read():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return await loop.run_in_executor(None, _read)

async def save_guild(guild_id: int, data: Dict[str, Any]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(guild_id)
    lock = _locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        loop = asyncio.get_running_loop()
        def _write():
            tmp = p.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        await loop.run_in_executor(None, _write)

async def set_activated(guild_id: int, value: bool):
    data = await load_guild(guild_id)
    data["activated"] = bool(value)
    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await save_guild(guild_id, data)
