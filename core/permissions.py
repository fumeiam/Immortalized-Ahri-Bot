from typing import Any
from . import db

async def is_guild_admin(user: Any, guild_id: int) -> bool:
    # owner is always admin
    try:
        if hasattr(user, "guild") and user.guild and user.id == user.guild.owner_id:
            return True
    except Exception:
        pass
    data = await db.load_guild(guild_id)
    return int(user.id) in set(int(x) for x in data.get("admins", []))

async def ensure_owner_admin(guild):
    if not guild:
        return
    data = await db.load_guild(guild.id)
    if not data.get("admins"):
        data["admins"] = [guild.owner_id]
        await db.save_guild(guild.id, data)
