from __future__ import annotations
from typing import List, Optional
import discord
from core import db, personality, utils

FEATURE_INFO = {"name": "reaction_roles", "triggers": ["reactionrole"]}

def register(bot, key, func):
    bot.trigger_handlers[key] = func

def normalize_emoji(tok: str) -> Optional[str]:
    try:
        pe = discord.PartialEmoji.from_str(tok)
        return str(pe)
    except Exception:
        if tok and not tok.isspace():
            return tok
        return None

async def setup(bot):
    async def rr_cmd(bot, message: discord.Message, args: List[str]):
        if not args:
            await message.channel.send('Use: `ahri reactionrole create "Title" #channel` | `add <message_id> <emoji> "Role Name"` | `remove <message_id> <emoji>` | `list`')
            return
        sub = args[0].lower()
        if sub == "create":
            if not message.channel_mentions:
                await message.channel.send("Mention a channel for the panel.")
                return
            ch = message.channel_mentions[0]
            title = "Choose a role!"
            if '"' in message.content:
                try:
                    title = message.content.split('"')[1]
                except Exception:
                    pass
            try:
                msg = await ch.send(f"**{title}**\nReact to get the role!")
                g = await db.load_guild(message.guild.id)
                g["settings"]["reaction_roles"]["panels"].append({"message_id": msg.id, "channel_id": ch.id, "map": {}})
                await db.save_guild(message.guild.id, g)
                await message.channel.send(personality.ahri_say("done") + f" Panel ID: `{msg.id}`")
            except Exception:
                await message.channel.send("Couldn't create panel. Check permissions.")
            return
        if sub == "add" and len(args) >= 4:
            try:
                message_id = int(args[1])
            except ValueError:
                await message.channel.send("Invalid message id.")
                return
            emoji_tok = args[2]
            role_name = None
            if '"' in message.content:
                try:
                    role_name = message.content.split('"')[1]
                except Exception:
                    pass
            if not role_name:
                await message.channel.send('Give role name in quotes: "Role Name"')
                return
            norm = normalize_emoji(emoji_tok)
            if not norm:
                await message.channel.send("Emoji not recognized.")
                return
            role = discord.utils.get(message.guild.roles, name=role_name)
            if not role:
                await message.channel.send("Role not found. Create it first.")
                return
            g = await db.load_guild(message.guild.id)
            panel = next((p for p in g["settings"]["reaction_roles"]["panels"] if p["message_id"] == message_id), None)
            if not panel:
                await message.channel.send("Panel not found.")
                return
            ch = message.guild.get_channel(panel["channel_id"])
            try:
                msg = await ch.fetch_message(message_id)
                await msg.add_reaction(norm)
            except Exception:
                await message.channel.send("Couldn't add reaction to message (missing perms?).")
                return
            panel["map"][norm] = role.name
            await db.save_guild(message.guild.id, g)
            await message.channel.send(personality.ahri_say("done"))
            return
        if sub == "remove" and len(args) >= 3:
            try:
                message_id = int(args[1])
            except Exception:
                await message.channel.send("Invalid message id.")
                return
            norm = normalize_emoji(args[2])
            if not norm:
                await message.channel.send("Emoji not recognized.")
                return
            g = await db.load_guild(message.guild.id)
            panel = next((p for p in g["settings"]["reaction_roles"]["panels"] if p["message_id"] == message_id), None)
            if not panel or norm not in panel["map"]:
                await message.channel.send("Mapping not found.")
                return
            del panel["map"][norm]
            await db.save_guild(message.guild.id, g)
            await message.channel.send(personality.ahri_say("done"))
            return
        if sub == "list":
            g = await db.load_guild(message.guild.id)
            panels = g["settings"]["reaction_roles"]["panels"]
            if not panels:
                await message.channel.send("No panels yet.")
                return
            lines = []
            for p in panels:
                pairs = [f"{k} -> {v}" for k,v in p["map"].items()] or ["(empty)"]
                lines.append(f"ID `{p['message_id']}` in <#{p['channel_id']}>: " + ", ".join(pairs))
            await message.channel.send("\n".join(lines))
            return
        await message.channel.send('Use: `ahri reactionrole create "Title" #channel` | `add <message_id> <emoji> "Role Name"` | `remove <message_id> <emoji>` | `list`')

    @bot.listen("on_raw_reaction_add")
    async def _add(payload: discord.RawReactionActionEvent):
        if payload.user_id == bot.user.id:
            return
        guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        g = await db.load_guild(guild.id)
        if not g.get("activated", False):
            return
        panels = g["settings"]["reaction_roles"]["panels"]
        if not panels:
            return
        key = str(payload.emoji)
        for p in panels:
            if p["message_id"] == payload.message_id and p["channel_id"] == payload.channel_id:
                role_name = p["map"].get(key)
                if not role_name:
                    continue
                role = discord.utils.get(guild.roles, name=role_name)
                if not role:
                    continue
                member = guild.get_member(payload.user_id)
                if member and not member.bot:
                    try:
                        await member.add_roles(role, reason="Reaction role")
                    except Exception:
                        pass

    @bot.listen("on_raw_reaction_remove")
    async def _remove(payload: discord.RawReactionActionEvent):
        if payload.user_id == bot.user.id:
            return
        guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        g = await db.load_guild(guild.id)
        if not g.get("activated", False):
            return
        panels = g["settings"]["reaction_roles"]["panels"]
        if not panels:
            return
        key = str(payload.emoji)
        for p in panels:
            if p["message_id"] == payload.message_id and p["channel_id"] == payload.channel_id:
                role_name = p["map"].get(key)
                if not role_name:
                    continue
                role = discord.utils.get(guild.roles, name=role_name)
                if not role:
                    continue
                member = guild.get_member(payload.user_id)
                if member and not member.bot:
                    try:
                        await member.remove_roles(role, reason="Reaction role removal")
                    except Exception:
                        pass

    register(bot, "reactionrole", rr_cmd)
