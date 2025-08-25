import random
LINES = {
    "inactive_hint": ["I’m sleepy~ An admin should use `/activate` to wake me up.", "Not charmed yet—an admin must `/activate` me."],
    "unknown_trigger": ["That command `{cmd}` isn't in my grimoire. Try `/help`.", "I don't know `{cmd}`—peek `/help`, cutie~"],
    "no_permission": ["Ah-ah~ Only my chosen can use that. Ask an admin.", "You don't have the charm for that command."],
    "oops": ["Eep—my tail slipped. Try again?", "Something went poof. I’ll behave next time~"],
    "activated": ["All warmed up. Let’s play~ ✨", "I’m awake and ready to mischief!"],
    "deactivated": ["Going quiet. Call me when you need me~", "Shh… I’ll curl up for a nap now."],
    "help_intro": "Nine tails, many tricks. Here’s what I can do:",
    "done": ["Done~", "As you wish, darling."],
}

def ahri_say(key: str, **kwargs) -> str:
    arr = LINES.get(key, ["…"])
    line = random.choice(arr)
    return line.format(**kwargs)
