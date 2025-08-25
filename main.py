#!/usr/bin/env python3
import re, asyncio, logging
from typing import Dict, Callable, Any, List

import discord
from discord import app_commands
from discord.ext import commands

from core import config, db, loader, personality, permissions, utils

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.message_content = True

TRIGGER = "ahri"

class AhriBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or(TRIGGER + " "),
            intents=INTENTS,
            help_command=None,
            case_insensitive=True,
        )
        self.trigger_handlers: Dict[str, Callable] = {}
        self.feature_info: Dict[str, Dict[str, Any]] = {}
        self.failed_modules: List[str] = []

    async def setup_hook(self):
        # configure logging already done by import
        await loader.load_features(self)
        try:
            await self.tree.sync()
        except Exception as e:
            logging.exception("Slash sync failed: %s", e)

    async def on_ready(self):
        logging.getLogger().info("Ready as %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name="with nine tails ✨"))

    async def on_guild_join(self, guild: discord.Guild):
        await db.ensure_guild(guild.id)

    async def on_message(self, message: discord.Message):
        # ignore bots & DMs
        if message.author.bot or message.guild is None:
            return

        content = (message.content or "").strip()
        m = re.match(rf'(?i)^\s*{re.escape(TRIGGER)}\b', content)
        if not m:
            # allow other commands (if any)
            try:
                await self.process_commands(message)
            except Exception:
                pass
            return

        rest = content[m.end():].strip()
        if not rest:
            try:
                await message.channel.send(personality.ahri_say("help_intro"))
            except Exception:
                pass
            return

        tokens = utils.tokenize(rest)
        if not tokens:
            await message.channel.send(personality.ahri_say("unknown_trigger", cmd=rest.split()[0] if rest else ""))
            return

        # activation gate
        guild_id = message.guild.id
        g = await db.load_guild(guild_id)
        if not g.get("activated", False):
            await message.channel.send(personality.ahri_say("inactive_hint"))
            return

        cmd = tokens.pop(0).lower()
        handler = self.trigger_handlers.get(cmd)
        if not handler:
            await message.channel.send(personality.ahri_say("unknown_trigger", cmd=cmd))
            return

        try:
            needs_admin = getattr(handler, "_needs_admin", False)
            if needs_admin and not await permissions.is_guild_admin(message.author, guild_id):
                await message.channel.send(personality.ahri_say("no_permission"))
                return
            await handler(self, message, tokens)
        except Exception as e:
            logging.exception("Trigger handler error for %s: %s", cmd, e)
            try:
                await message.channel.send(personality.ahri_say("oops"))
            except Exception:
                pass
        finally:
            try:
                await self.process_commands(message)
            except Exception:
                pass

bot = AhriBot()

# Slash: activate, deactivate, help
@bot.tree.command(name="activate", description="Activate AhriBot features for this server (admin-only)")
@app_commands.checks.has_permissions(administrator=True)
async def activate(interaction: discord.Interaction):
    await db.set_activated(interaction.guild_id, True)
    await permissions.ensure_owner_admin(interaction.guild)
    await interaction.response.send_message(personality.ahri_say("activated"), ephemeral=True)

@bot.tree.command(name="deactivate", description="Deactivate AhriBot features for this server (admin-only)")
@app_commands.checks.has_permissions(administrator=True)
async def deactivate(interaction: discord.Interaction):
    await db.set_activated(interaction.guild_id, False)
    await interaction.response.send_message(personality.ahri_say("deactivated"), ephemeral=True)

@bot.tree.command(name="help", description="Show AhriBot features and trigger commands")
async def help_cmd(interaction: discord.Interaction):
    info_lines = ["**Slash commands**: `/activate`, `/deactivate`, `/help`"]
    if bot.feature_info:
        info_lines.append("**Features loaded:**")
        for name, meta in bot.feature_info.items():
            triggers = ", ".join(meta.get("triggers", [])) or "—"
            info_lines.append(f"• **{name}** → `{triggers}`")
    if bot.failed_modules:
        info_lines.append("⚠️ Failed modules: " + ", ".join(bot.failed_modules))
    await interaction.response.send_message(personality.ahri_say("help_intro") + "\n" + "\n".join(info_lines), ephemeral=True)

def main():
    cfg = config.load_env()
    config.ensure_data_dir()
    bot.run(cfg.token)

if __name__ == "__main__":
    main()
