from __future__ import annotations
from typing import List, Any
import discord
from core import db, personality, utils

FEATURE_INFO = {"name": "admin_tools", "triggers": ["setadmin", "removeadmin", "listadmins", "kick", "ban", "mute", "unmute", "create", "assign", "remove", "rename", "log"]}

def register(bot, key, func):
    bot.trigger_handlers[key] = func

def _admin(func):
    return utils.admin_only(func)

async def setup(bot):
    @_admin
    async def setadmin(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to set as admin.")
            return
        target = message.mentions[0]
        g = await db.load_guild(message.guild.id)
        admins = set(int(x) for x in g.get("admins", []))
        admins.add(target.id)
        g["admins"] = list(admins)
        await db.save_guild(message.guild.id, g)
        await message.channel.send(personality.ahri_say("done"))

    @_admin
    async def removeadmin(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to remove from admins.")
            return
        target = message.mentions[0]
        g = await db.load_guild(message.guild.id)
        admins = set(int(x) for x in g.get("admins", []))
        admins.discard(target.id)
        g["admins"] = list(admins)
        await db.save_guild(message.guild.id, g)
        await message.channel.send(personality.ahri_say("done"))

    async def listadmins(bot, message: discord.Message, args: List[str]):
        g = await db.load_guild(message.guild.id)
        ids = g.get("admins", [])
        if not ids:
            await message.channel.send("No special charmers yet~")
            return
        names = []
        for i in ids:
            m = message.guild.get_member(int(i))
            names.append(m.mention if m else f"`{i}`")
        await message.channel.send("Admins: " + ", ".join(names))

    @_admin
    async def kick(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to kick.")
            return
        try:
            await message.guild.kick(message.mentions[0], reason="Kicked by AhriBot")
            await message.channel.send(personality.ahri_say("done"))
        except Exception:
            await message.channel.send("I couldn't kick them (missing perms?).")

    @_admin
    async def ban(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to ban.")
            return
        try:
            await message.guild.ban(message.mentions[0], reason="Banned by AhriBot", delete_message_days=0)
            await message.channel.send(personality.ahri_say("done"))
        except Exception:
            await message.channel.send("I couldn't ban them (missing perms?).")

    @_admin
    async def mute(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to mute.")
            return
        minutes = 5
        for a in args:
            if a.isdigit():
                minutes = int(a)
                break
        try:
            until = discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutes)
            await message.mentions[0].timeout(until, reason="Muted by AhriBot")
            await message.channel.send(personality.ahri_say("done"))
        except Exception:
            await message.channel.send("Couldn't timeout that user (missing perms?).")

    @_admin
    async def unmute(bot, message: discord.Message, args: List[str]):
        if not message.mentions:
            await message.channel.send("Mention a user to unmute.")
            return
        try:
            await message.mentions[0].timeout(None, reason="Unmuted by AhriBot")
            await message.channel.send(personality.ahri_say("done"))
        except Exception:
            await message.channel.send("Couldn't unmute that user.")

    @_admin
    async def create(bot, message: discord.Message, args: List[str]):
        if len(args) >= 2 and args[0].lower() == "role":
            name = " ".join(args[1:]).strip('"')
            await message.guild.create_role(name=name)
            await message.channel.send(personality.ahri_say("done"))
        elif len(args) >= 2 and args[0].lower() == "channel":
            name = " ".join(args[1:]).strip('"')
            await message.guild.create_text_channel(name=name)
            await message.channel.send(personality.ahri_say("done"))
        else:
            await message.channel.send('Use: `ahri create role "Name"` or `ahri create channel "name"`')

    @_admin
    async def assign(bot, message: discord.Message, args: List[str]):
        if not message.mentions or len(args) < 2:
            await message.channel.send('Use: `ahri assign @user "Role Name"`')
            return
        user = message.mentions[0]
        role_name = " ".join(a for a in args if not a.startswith("<@")).strip('"')
        role = discord.utils.get(message.guild.roles, name=role_name)
        if not role:
            await message.channel.send("Role not found.")
            return
        await user.add_roles(role, reason="Assigned by AhriBot")
        await message.channel.send(personality.ahri_say("done"))

    @_admin
    async def remove(bot, message: discord.Message, args: List[str]):
        if not message.mentions or len(args) < 2:
            await message.channel.send('Use: `ahri remove @user "Role Name"`')
            return
        user = message.mentions[0]
        role_name = " ".join(a for a in args if not a.startswith("<@")).strip('"')
        role = discord.utils.get(message.guild.roles, name=role_name)
        if not role:
            await message.channel.send("Role not found.")
            return
        await user.remove_roles(role, reason="Removed by AhriBot")
        await message.channel.send(personality.ahri_say("done"))

    @_admin
    async def rename(bot, message: discord.Message, args: List[str]):
        if len(args) >= 3 and args[0].lower() == "channel":
            ch = message.channel_mentions[0] if message.channel_mentions else message.channel
            new_name = " ".join(args[1:]).strip('"')
            await ch.edit(name=new_name)
            await message.channel.send(personality.ahri_say("done"))
        else:
            await message.channel.send('Use: `ahri rename channel #channel "New Name"`')

    @_admin
    async def log(bot, message: discord.Message, args: List[str]):
        if len(args) >= 1 and args[0].lower() == "set" and message.channel_mentions:
            ch = message.channel_mentions[0]
            g = await db.load_guild(message.guild.id)
            g["settings"]["logging"]["channel_id"] = ch.id
            await db.save_guild(message.guild.id, g)
            await message.channel.send(personality.ahri_say("done"))
        else:
            await message.channel.send('Use: `ahri log set #channel`')

    # register handlers
    for k, fn in {"setadmin": setadmin, "removeadmin": removeadmin, "listadmins": listadmins, "kick": kick, "ban": ban, "mute": mute, "unmute": unmute, "create": create, "assign": assign, "remove": remove, "rename": rename, "log": log}.items():
        register(bot, k, fn)
