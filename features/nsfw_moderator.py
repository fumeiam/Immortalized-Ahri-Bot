from __future__ import annotations

import os
import random
import time
import json
import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from dotenv import load_dotenv
from discord.ext import commands  # to properly catch CommandNotFound

from core import db, utils, personality, permissions

AHRI_FEEDBACK_RESPONSES = [
    "Mmm~ that was a little too spicy for here ♥ I’ll be taking it down~",
    "Oh my~ naughty naughty… I’ll clean this up for you ♥",
    "Ehehe~ that one’s a bit too much for the den, let’s keep it safe ♥"
]

# Load environment variables
load_dotenv()

FEATURE_INFO = {
    "name": "nsfw_moderator",
    "triggers": ["nsfw"],
    "description": "Scan images for NSFW content, delete/report them, and provide admin trigger commands."
}

# --- Provider interface (Sightengine only) ---
class NSFWProvider:
    async def check_image(self, session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        raise NotImplementedError()

class SightengineProvider(NSFWProvider):
    def __init__(self, api_user: str, api_secret: str):
        self.api_user = api_user
        self.api_secret = api_secret
        self.endpoint = "https://api.sightengine.com/1.0/check.json"

    async def check_image(self, session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        # include 'type' model to distinguish photos vs illustrations
        params = {
            "models": "nudity-2.0,type",
            "api_user": self.api_user,
            "api_secret": self.api_secret,
            "url": url,
        }
        try:
            async with session.get(self.endpoint, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {"ok": False, "error": f"Sightengine HTTP {resp.status}: {text[:300]}"}
                try:
                    data = json.loads(text)
                except Exception:
                    return {"ok": False, "error": "Sightengine returned non-JSON"}
                return {"ok": True, "data": data}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Sightengine timeout"}
        except Exception as e:
            return {"ok": False, "error": f"Sightengine error: {e}"}

# --- module-level session + semaphore ---
_session: Optional[aiohttp.ClientSession] = None
_scan_sem = asyncio.Semaphore(4)

async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

def _get_env_provider() -> Optional[NSFWProvider]:
    """
    Sightengine only. Expects SIGHTENGINE_USER and SIGHTENGINE_SECRET.
    """
    se_user = os.getenv("SIGHTENGINE_USER")
    se_secret = os.getenv("SIGHTENGINE_SECRET")

    if se_user and se_secret:
        print("Using Sightengine provider")
        return SightengineProvider(se_user, se_secret)

    print("Warning: No NSFW provider configured. Set SIGHTENGINE_USER and SIGHTENGINE_SECRET")
    return None

# --- per-guild config namespace inside core db ---
NSFW_KEY = "nsfw_moderator"

def _ensure_nsfw_cfg(guild_data: Dict[str, Any]) -> Dict[str, Any]:
    part = guild_data.setdefault(NSFW_KEY, {})
    part.setdefault("enabled", True)
    part.setdefault("log_channel_id", None)
    part.setdefault("active_channel_ids", [])
    part.setdefault("whitelist_user_ids", [])
    part.setdefault("blacklist_user_ids", [])
    part.setdefault("everyone_blacklisted", False)
    # thresholds for realistic photos and illustrations (anime/comics)
    part.setdefault("thresholds", {
        "nsfw": 0.80,
        "suggestive": 0.90,
        "nsfw_illustration": 0.90,
        "suggestive_illustration": 0.95,
    })
    part.setdefault("last_updated", None)
    return part

# --- helper parsing functions (defensive) ---
async def _parse_sightengine_scores(data: Dict[str, Any]) -> Tuple[float, float, str]:
    # Defensive parsing that tolerates nested dicts or changing schema.
    def _as_float(v: Any) -> float:
        try:
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, dict):
                nums = [float(x) for x in v.values() if isinstance(x, (int, float))]
                return max(nums) if nums else 0.0
            return 0.0
        except Exception:
            return 0.0

    try:
        nud = data.get("nudity", {}) or {}
        sexual_activity = _as_float(nud.get("sexual_activity", 0.0))
        sexual_display = _as_float(nud.get("sexual_display", 0.0))
        erotica = _as_float(nud.get("erotica", 0.0))
        suggestive_raw = data.get("suggestive", nud.get("suggestive", 0.0))
        suggestive = _as_float(suggestive_raw)
        explicit = max(sexual_activity, sexual_display, erotica)

        # Image type (photo vs illustration)
        media_type = "photo"
        t = data.get("type")
        if isinstance(t, dict) and t:
            # choose the key with the highest probability
            media_type = max(t, key=lambda k: (t.get(k) if isinstance(t.get(k), (int, float)) else 0.0))
        elif isinstance(t, str):
            media_type = t

        return explicit, suggestive, media_type
    except Exception:
        return 0.0, 0.0, "photo"

# --- attachment heuristic ---
def _is_image_attachment(att: discord.Attachment) -> bool:
    ct = getattr(att, "content_type", None)
    if ct and ct.startswith("image/"):
        return True
    fn = getattr(att, "filename", "") or ""
    return bool(re.search(r"\.(png|jpe?g|gif|webp)$", fn, re.I))

# --- internal logging to configured log channel ---
async def _log_action(bot: "discord.Client", guild_id: int, text: str) -> None:
    try:
        g = await db.load_guild(guild_id)
        ns = _ensure_nsfw_cfg(g)
        cid = ns.get("log_channel_id")
        if not cid:
            return
        ch = bot.get_channel(cid)
        if not ch:
            return

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        log_message = f"`[{timestamp}]` {text}"  # keep link only, no embeds
        await ch.send(
            log_message,
            allowed_mentions=discord.AllowedMentions.none(),
            suppress_embeds=True
        )
    except Exception as e:
        try:
            print(f"[NSFW Logger Error] {e}")
        except Exception:
            pass
        return

# --- core scanning routine ---
async def _scan_message(bot: "discord.Client", message: discord.Message, provider: Optional[NSFWProvider]) -> bool:
    if message.guild is None or message.author.bot:
        return False

    try:
        gdata = await db.load_guild(message.guild.id)
    except Exception as e:
        await _log_action(bot, message.guild.id if message.guild else 0, f"❌ Failed to load guild data: {e}")
        return False

    if not gdata.get("activated", False):
        return False

    ns = _ensure_nsfw_cfg(gdata)
    if not ns.get("enabled", True):
        return False

    author_id = message.author.id
    if author_id in ns.get("whitelist_user_ids", []):
        return False

    attachments = [a for a in message.attachments if _is_image_attachment(a)]
    if not attachments:
        return False

    is_monitored_channel = message.channel.id in ns.get("active_channel_ids", [])
    is_explicitly_blacklisted = author_id in ns.get("blacklist_user_ids", [])
    everyone_blacklisted = ns.get("everyone_blacklisted", False)

    # --- scanning rules (as in original) ---
    if not is_monitored_channel:
        if not is_explicitly_blacklisted:
            return False
    else:
        if not everyone_blacklisted and not is_explicitly_blacklisted:
            return False

    if provider is None:
        await _log_action(bot, message.guild.id, "⚠️ Provider not configured; skipping image scan.")
        return False

    session = await _get_session()
    async with _scan_sem:
        for att in attachments:
            try:
                res = await provider.check_image(session, att.url)
                if not res.get("ok"):
                    await _log_action(
                        bot, message.guild.id,
                        f"❌ Scan failed for image `{att.url}` — {res.get('error', 'Unknown error')}"
                    )
                    continue

                data = res.get("data", {})
                nsfw_score, suggestive_score, media_type = await _parse_sightengine_scores(data)

                thresholds = ns.get("thresholds", {"nsfw": 0.80, "suggestive": 0.90, "nsfw_illustration": 0.90, "suggestive_illustration": 0.95})

                # Choose thresholds based on media type (treat non-realistic images as illustrations)
                illustration_labels = {"illustration", "cartoon", "anime", "animated", "cgi"}
                if str(media_type).lower() in illustration_labels:
                    nsfw_th = thresholds.get("nsfw_illustration", 0.90)
                    sugg_th = thresholds.get("suggestive_illustration", 0.95)
                    typ_label = "illustration"
                else:
                    nsfw_th = thresholds.get("nsfw", 0.80)
                    sugg_th = thresholds.get("suggestive", 0.90)
                    typ_label = "photo"

                # --- Improved decision logic to reduce false positives ---
                NSFW_MARGIN = 0.05          # require nsfw to exceed threshold by a buffer
                SUGGESTIVE_MARGIN = 0.10    # suggestive-only needs higher certainty
                NSFW_SUGG_GAP = 0.15        # near-threshold nsfw must be clearly above suggestive

                should_delete = False

                # Strong NSFW signal
                if nsfw_score >= nsfw_th + NSFW_MARGIN:
                    should_delete = True
                # Near-threshold NSFW but clearly above suggestive noise
                elif nsfw_score >= nsfw_th and (nsfw_score - suggestive_score) > NSFW_SUGG_GAP:
                    should_delete = True
                # Suggestive-only: log but don't delete unless extremely high
                elif suggestive_score >= sugg_th + SUGGESTIVE_MARGIN:
                    await _log_action(
                        bot, message.guild.id,
                        f"⚠️ Suggestive image flagged (not deleted) from {message.author} in <#{message.channel.id}> "
                        f"(url={att.url}, nsfw={nsfw_score:.2f}, suggestive={suggestive_score:.2f}, type={typ_label})"
                    )
                    should_delete = False

                if should_delete:
                    deleted = False
                    try:
                        await message.delete()
                        deleted = True
                    except Exception as e:
                        deleted = False
                        await _log_action(
                            bot, message.guild.id,
                            f"❌ Error deleting message with image `{att.url}` in <#{message.channel.id}>: {e}"
                        )

                    # Ahri-style feedback only (randomized, no mention)
                    try:
                        response = random.choice(AHRI_FEEDBACK_RESPONSES)
                        await message.channel.send(
                            personality.ahri_say("oops") + f" {response}",
                            delete_after=12
                        )
                    except Exception:
                        pass

                    await _log_action(
                        bot, message.guild.id,
                        (
                            f"🚨 Deleted NSFW image from {message.author} in <#{message.channel.id}> "
                            f"(url={att.url}, nsfw={nsfw_score:.2f}/{nsfw_th}, "
                            f"suggestive={suggestive_score:.2f}/{sugg_th}, type={typ_label})"
                            if deleted else
                            f"⚠️ Flagged NSFW image from {message.author} in <#{message.channel.id}> "
                            f"(url={att.url}, nsfw={nsfw_score:.2f}/{nsfw_th}, "
                            f"suggestive={suggestive_score:.2f}/{sugg_th}, type={typ_label}) — not deleted."
                        )
                    )

                    ns["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    try:
                        await db.save_guild(message.guild.id, gdata)
                    except Exception as e:
                        await _log_action(bot, message.guild.id, f"❌ Failed to save guild settings: {e}")

                    return deleted
            except Exception as e:
                await _log_action(
                    bot, message.guild.id,
                    f"❌ Exception while scanning image in <#{message.channel.id}>: {e}"
                )
                continue
    return False

# --- trigger handler registration helper (matches other features) ---
def register(bot, key: str, func):
    # ensure dict exists to avoid AttributeError
    if not hasattr(bot, "trigger_handlers") or bot.trigger_handlers is None:
        bot.trigger_handlers = {}
    bot.trigger_handlers[key] = func

# --- the main setup entrypoint called by loader ---
async def setup(bot: "discord.Client"):
    provider = _get_env_provider()

    # ---- message listener ----
    @bot.listen("on_message")
    async def _on_message_listener(message: discord.Message):
        try:
            if message.author.bot or message.guild is None:
                return
            gdata = await db.load_guild(message.guild.id)
            if not gdata.get("activated", False):
                return
            await _scan_message(bot, message, provider)
        except commands.CommandNotFound:
            # suppress CommandNotFound console spam
            return
        except Exception as e:
            try:
                await _log_action(bot, message.guild.id if message.guild else 0, f"❌ on_message error: {e}")
            except Exception:
                pass
            return

    # ---- trigger root: ahri nsfw <sub> ----
    async def nsfw_root(bot: "discord.Client", message: discord.Message, args: List[str]):
        if message.guild is None:
            return

        if not args:
            await message.channel.send("Usage: `ahri nsfw help` or `ahri nsfw <subcommand>`")
            return

        sub = args[0].lower()
        gdata = await db.load_guild(message.guild.id)
        ns = _ensure_nsfw_cfg(gdata)

        async def _save_and_ack(text: str):
            ns["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            await db.save_guild(message.guild.id, gdata)
            await message.channel.send(personality.ahri_say("done") + " " + text)

        admin_subs = {
            "help", "h",
            "enable", "on", "disable", "off", "setlogchannel", "setthresholds",
            "addchannel", "monitor", "removechannel", "unmonitor",
            "whitelist", "unwhitelist", "allow", "unallow",
            "blacklist", "unblacklist", "watch", "unwatch",
            "toggleglobal", "globallock", "viewsettings", "settings",
            "viewwhitelist", "viewblacklist"
        }
        if sub in admin_subs:
            if not await permissions.is_guild_admin(message.author, message.guild.id):
                await message.channel.send(personality.ahri_say("no_permission"))
                return

        # HELP
        if sub in ("help", "h"):
            help_text = (
                "**Ahri NSFW Moderator — admin commands**\n"
                "`ahri nsfw help` — show this help\n\n"
                "`ahri nsfw enable|disable` — toggle scanning\n"
                "`ahri nsfw setlogchannel #channel` — where logs are sent\n"
                "`ahri nsfw setthresholds <nsfw> <suggestive> [nsfw_illustration] [suggestive_illustration]` — 0.0–1.0\n"
                "`ahri nsfw addchannel #channel` / `ahri nsfw removechannel #channel`\n"
                "`ahri nsfw whitelist @user` / `ahri nsfw unwhitelist @user`\n"
                "`ahri nsfw blacklist @user` / `ahri nsfw unblacklist @user`\n"
                "`ahri nsfw viewwhitelist` / `ahri nsfw viewblacklist`\n"
                "`ahri nsfw toggleglobal` — treat everyone as blacklisted in monitored channels (whitelist still bypasses)\n"
                "`ahri nsfw viewsettings` — view current settings\n"
            )
            await message.channel.send(help_text)
            return

        # enable/disable
        if sub in ("enable", "on"):
            ns["enabled"] = True
            await _save_and_ack("NSFW scanning enabled.")
            return
        if sub in ("disable", "off"):
            ns["enabled"] = False
            await _save_and_ack("NSFW scanning disabled.")
            return

        # setlogchannel
        if sub == "setlogchannel":
            if not message.channel_mentions:
                await message.channel.send("Mention the channel: `ahri nsfw setlogchannel #logs`")
                return
            ch = message.channel_mentions[0]
            ns["log_channel_id"] = ch.id
            await _save_and_ack(f"Logging to {ch.mention}.")
            return

        # setthresholds
        if sub == "setthresholds":
            if len(args) < 3:
                await message.channel.send("Usage: `ahri nsfw setthresholds <nsfw> <suggestive> [nsfw_illustration] [suggestive_illustration]` (0.0–1.0)")
                return
            try:
                nsfw_v = float(args[1])
                suggest_v = float(args[2])
                ill_nsfw_v = float(args[3]) if len(args) > 3 else ns["thresholds"].get("nsfw_illustration", 0.90)
                ill_sugg_v = float(args[4]) if len(args) > 4 else ns["thresholds"].get("suggestive_illustration", 0.95)
                if not (0.0 <= nsfw_v <= 1.0):
                    await message.channel.send("NSFW threshold must be between 0.0 and 1.0")
                    return
                if not (0.0 <= suggest_v <= 1.0):
                    await message.channel.send("Suggestive threshold must be between 0.0 and 1.0")
                    return
                if not (0.0 <= ill_nsfw_v <= 1.0) or not (0.0 <= ill_sugg_v <= 1.0):
                    await message.channel.send("Illustration thresholds must be between 0.0 and 1.0")
                    return
                ns["thresholds"] = {
                    "nsfw": nsfw_v,
                    "suggestive": suggest_v,
                    "nsfw_illustration": ill_nsfw_v,
                    "suggestive_illustration": ill_sugg_v,
                }
                await _save_and_ack(
                    f"Thresholds set: NSFW={nsfw_v:.2f}, Suggestive={suggest_v:.2f}, "
                    f"NSFW(illustration)={ill_nsfw_v:.2f}, Suggestive(illustration)={ill_sugg_v:.2f}"
                )
            except ValueError:
                await message.channel.send("Invalid thresholds; use numbers 0.0–1.0.")
            return

        # addchannel / removechannel
        if sub in ("addchannel", "monitor"):
            if not message.channel_mentions:
                await message.channel.send("Mention the channel to monitor: `ahri nsfw addchannel #channel`")
                return
            ch = message.channel_mentions[0]
            if ch.id not in ns.get("active_channel_ids", []):
                ns["active_channel_ids"].append(ch.id)
                await _save_and_ack(f"Monitoring {ch.mention}.")
            else:
                await message.channel.send(f"I'm already watching {ch.mention}~")
            return

        if sub in ("removechannel", "unmonitor"):
            if not message.channel_mentions:
                await message.channel.send("Mention the channel to stop: `ahri nsfw removechannel #channel`")
                return
            ch = message.channel_mentions[0]
            if ch.id in ns.get("active_channel_ids", []):
                ns["active_channel_ids"].remove(ch.id)
                await _save_and_ack(f"Stopped monitoring {ch.mention}.")
            else:
                await message.channel.send(f"I wasn't watching {ch.mention}~")
            return

        # whitelist / unwhitelist
        if sub in ("whitelist", "allow"):
            if not message.mentions:
                await message.channel.send("Mention the user to whitelist: `ahri nsfw whitelist @user`")
                return
            u = message.mentions[0]
            if u.id not in ns.get("whitelist_user_ids", []):
                ns["whitelist_user_ids"].append(u.id)
                await _save_and_ack(f"{u.mention} can bypass scans.")
            else:
                await message.channel.send(f"{u.mention} is already whitelisted~")
            return

        if sub in ("unwhitelist", "unallow"):
            if not message.mentions:
                await message.channel.send("Mention the user to remove from whitelist.")
                return
            u = message.mentions[0]
            if u.id in ns.get("whitelist_user_ids", []):
                ns["whitelist_user_ids"].remove(u.id)
                await _save_and_ack(f"{u.mention} removed from whitelist.")
            else:
                await message.channel.send(f"{u.mention} wasn't whitelisted~")
            return

        # blacklist / unblacklist
        if sub in ("blacklist", "watch"):
            if not message.mentions:
                await message.channel.send("Mention the user to blacklist: `ahri nsfw blacklist @user`")
                return
            u = message.mentions[0]
            if u.id not in ns.get("blacklist_user_ids", []):
                ns["blacklist_user_ids"].append(u.id)
                await _save_and_ack(f"{u.mention} added to watchlist.")
            else:
                await message.channel.send(f"{u.mention} is already on the watchlist~")
            return

        if sub in ("unblacklist", "unwatch"):
            if not message.mentions:
                await message.channel.send("Mention the user to remove from blacklist.")
                return
            u = message.mentions[0]
            if u.id in ns.get("blacklist_user_ids", []):
                ns["blacklist_user_ids"].remove(u.id)
                await _save_and_ack(f"{u.mention} removed from watchlist.")
            else:
                await message.channel.send(f"{u.mention} wasn't on the watchlist~")
            return

        # toggleglobal
        if sub in ("toggleglobal", "globallock"):
            ns["everyone_blacklisted"] = not ns.get("everyone_blacklisted", False)
            state = "ENABLED (monitored channels only) 🔒" if ns["everyone_blacklisted"] else "DISABLED 🔓"
            await _save_and_ack(f"Global 'everyone blacklisted' is now {state}")
            return

        # viewsettings
        if sub in ("viewsettings", "settings"):
            thr = ns.get("thresholds", {})
            monitored = ", ".join(f"<#{c}>" for c in ns.get("active_channel_ids", [])) or "(none)"
            logc = f"<#{ns['log_channel_id']}>" if ns.get("log_channel_id") else "(not set)"
            whitelist = " ".join(f"<@{uid}>" for uid in ns.get("whitelist_user_ids", [])) or "(empty)"
            blacklist = " ".join(f"<@{uid}>" for uid in ns.get("blacklist_user_ids", [])) or "(empty)"
            last_updated = ns.get("last_updated") or "(never)"

            await message.channel.send(
                f"Enabled: {'YES' if ns.get('enabled', True) else 'NO'}\n"
                f"Log channel: {logc}\n"
                f"Monitored: {monitored}\n"
                f"Global everyone-blacklisted: {'ON (monitored only)' if ns.get('everyone_blacklisted') else 'OFF'}\n"
                f"Thresholds → NSFW: {thr.get('nsfw',0):.2f} | Suggestive: {thr.get('suggestive',0):.2f} | "
                f"NSFW(illustration): {thr.get('nsfw_illustration',0.90):.2f} | "
                f"Suggestive(illustration): {thr.get('suggestive_illustration',0.95):.2f}\n"
                f"Whitelist: {whitelist}\n"
                f"Blacklist: {blacklist}\n"
                f"Last updated: {last_updated}"
            )
            return

        # viewwhitelist
        if sub == "viewwhitelist":
            wl = ns.get("whitelist_user_ids", [])
            if not wl:
                await message.channel.send("Whitelist is empty.")
            else:
                users = " ".join(f"<@{uid}>" for uid in wl)
                await message.channel.send(f"Whitelisted users: {users}")
            return

        # viewblacklist
        if sub == "viewblacklist":
            bl = ns.get("blacklist_user_ids", [])
            if not bl:
                await message.channel.send("Blacklist is empty.")
            else:
                users = " ".join(f"<@{uid}>" for uid in bl)
                await message.channel.send(f"Blacklisted users: {users}")
            return

        await message.channel.send("I don't recognize that subcommand. Try `ahri nsfw help`.")

    # register handler
    register(bot, "nsfw", nsfw_root)

    # --- graceful aiohttp session cleanup on bot shutdown ---
    original_close = bot.close

    async def wrapped_close():
        global _session
        if _session and not _session.closed:
            try:
                await _session.close()
            except Exception:
                pass
            _session = None
        await original_close()

    bot.close = wrapped_close
    return
