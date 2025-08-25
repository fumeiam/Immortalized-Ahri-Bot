from __future__ import annotations
from typing import List
import discord
from core import db, personality, utils

FEATURE_INFO = {"name": "automod", "triggers": ["automod"]}

def register(bot, key, func):
    bot.trigger_handlers[key] = func

async def setup(bot):
    @bot.listen("on_message")
    async def _automod(message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        data = await db.load_guild(message.guild.id)
        if not data.get("activated", False):
            return
        cfg = data["settings"]["automod"]
        if not cfg.get("enabled", False):
            return
        text = (message.content or "").lower()
        for w in cfg.get("banned_words", []):
            if w and w.lower() in text:
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await message.channel.send(f"Shh~ That word is banned here, {message.author.mention}.")
                except Exception:
                    pass
                return

    async def automod_cmd(bot, message: discord.Message, args: List[str]):
        from core import permissions
        if not await permissions.is_guild_admin(message.author, message.guild.id):
            await message.channel.send(personality.ahri_say("no_permission"))
            return
        if not args:
            await message.channel.send("Usage: `ahri automod on|off|addword <w>|removeword <w>|list`")
            return
        sub = args[0].lower()
        g = await db.load_guild(message.guild.id)
        cfg = g["settings"]["automod"]
        if sub == "on":
            cfg["enabled"] = True
        elif sub == "off":
            cfg["enabled"] = False
        elif sub == "addword" and len(args) >= 2:
            word = args[1]
            if word not in cfg["banned_words"]:
                cfg["banned_words"].append(word)
        elif sub == "removeword" and len(args) >= 2:
            try:
                cfg["banned_words"].remove(args[1])
            except Exception:
                pass
        elif sub == "list":
            await message.channel.send("Banned words: " + ", ".join(cfg["banned_words"]) or "none")
            return
        else:
            await message.channel.send("Usage: `ahri automod on|off|addword <w>|removeword <w>|list`")
            return
        await db.save_guild(message.guild.id, g)
        await message.channel.send(personality.ahri_say("done"))

    register(bot, "automod", automod_cmd)
