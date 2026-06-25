"""
Centralized emoji registry for Moddy.
All custom Discord emojis are defined here.
To change an emoji, update it in this file — it will propagate everywhere.
"""

# =============================================================================
# CORE UI EMOJIS
# =============================================================================

# Status indicators
DONE = "<:done:1398729525277229066>"
UNDONE = "<:undone:1398729502028333218>"
ERROR = "<:error:1519790252594827264>"
WARNING = "<:warning:1519789903100121139>"
INFO = "<:info:1519793991045091388>"
LOADING = "<a:loading:1455219844080336907>"

# Status dots
GREEN_STATUS = "<:green_status:1450929035428495505>"
YELLOW_STATUS = "<:yellow_status:1450929037542166669>"
RED_STATUS = "<:red_status:1450929038758772940>"

# Actions
ADD = "<:add:1519791773235413022>"
EDIT = "<:edit:1519795936568676383>"
DELETE = "<:delete:1519795753164210447>"
SAVE = "<:save:1444101502154182778>"
SEARCH = "<:search:1519790418290675822>"
SYNC = "<:sync:1398729150885269546>"
BACK = "<:back:1519795556665397431>"
NEXT = "<:next:1519791619526754354>"
PAUSE = "<:pause:1519787332801269811>"
PLAY = "<:play:1519787501789773996>"
LOGOUT = "<:logout:1519795093605974098>"
DOWNLOAD = "<:download:1519796568255889578>"
REPLY = "<:reply:1519790151130415154>"

# Navigation / UI
SETTINGS = "<:settings:1398729549323440208>"
COMMANDS = "<:commands:1519794878933106830>"
TOGGLE_ON = "<:toogle_on:1446267419034386473>"
TOGGLE_OFF = "<:toggle_off:1446267399786594514>"
REQUIRED_FIELDS = "<:required_fields:1446549185385074769>"

# Objects / Concepts
USER = "<:user:1398729712204779571>"
DEV = "<:dev:1398729645557285066>"
MODDY = "<:moddy:1396880909117947924>"
MODDY_ALT = "<:moddy:1451280939412881508>"
BLACKLIST = "<:blacklist:1519797375470669944>"
TIME = "<:time:1398729780723060736>"
SNOWFLAKE = "<:snowflake:1519797680069541999>"
WEB = "<:web:1398729801061240883>"
HISTORY = "<:history:1519796822963392755>"
BOOK = "<:book:1519788969691316467>"
CODE = "<:code:1519794490750271641>"
BUG = "<:bug:1519794242296483952>"
VERIFIED = "<:verified:1495533349266264230>"
VERIFIED_ORG = "<:verified_org:1495537358337081465>"
VERIFIED_ORG_MEMBER = "<:verified:1495533349266264230>"  # same visual as VERIFIED
MINI_VERIFIED = "<:miniverified:1439667456737280021>"
NOTE = "<:note:1519790932663468184>"
MESSAGE = "<:message:1519790643784843416>"
GROUPS = "<:groups:1519789805456724049>"
WAVING_HAND = "<:waving_hand:1519789691711393982>"
FLAG = "<:flag:1519789496181461183>"
AT = "<:at:1519789374898700289>"
STAR = "<:star:1519789099286925406>"
EMOJI = "<:emoji:1398729407065100359>"
PREMIUM = "<:premium:1519795224493424893>"
SUPPORT = "<:support:1519797524813185164>"
BALANCE = "<:balance:1398729232862941445>"
MANAGE_USER = "<:manageuser:1398729745293774919>"
BANNER = "<:banner:1519792732716007634>"
TEXT = "<:text:1519791921462120601>"
TRANSLATE = "<:translate:1398720130950627600>"
WEBHOOK = "<:webhook:1519793325329219675>"
STAFF = "<:staff:1398729432759476245>"

# =============================================================================
# SOCIAL PLATFORMS (Social Notifications module)
# -----------------------------------------------------------------------------
# NOTE: These are PLACEHOLDER ids — replace each one with the real custom emoji
# for the platform. Everything in the codebase references these constants, so
# updating the id here propagates everywhere. Also update PLATFORM_EMOJIS below
# stays in sync automatically (it references the constants).
# =============================================================================
SOCIAL = "<:web:1398729801061240883>"           # generic "social notifications" icon (placeholder — no custom emoji yet)
YOUTUBE = "<:youtube:1515511923066671185>"
TWITCH = "<:twitch:1515511921938399343>"
BLUESKY = "<:bluesky:1515511920235516024>"
RSS = "<:rss:1519788506980028477>"
INSTAGRAM = "<:web:1398729801061240883>"         # TODO replace with <:instagram:...> (platform is future/disabled)

# platform id -> emoji (single source of truth used by the module + config UI)
PLATFORM_EMOJIS = {
    "youtube": YOUTUBE,
    "twitch": TWITCH,
    "bluesky": BLUESKY,
    "rss": RSS,
    "instagram": INSTAGRAM,
}


def get_platform_emoji(platform: str) -> str:
    """Return the custom emoji for a social platform (falls back to SOCIAL)."""
    return PLATFORM_EMOJIS.get(platform, SOCIAL)

# =============================================================================
# STAFF & MODDY BADGES
# =============================================================================

DEV_BADGE = "<:dev_badge:1437514335009247274>"
MANAGER_BADGE = "<:manager_badge:1437514336355483749>"
MODDYTEAM_BADGE = "<:moddyteam_badge:1437514344467398837>"
SUPERVISOR_BADGE = "<:supervisor_badge:1437514346476470405>"
SUPPORT_SUPERVISOR_BADGE = "<:support_supervisor_badge:1437514347923636435>"
MOD_SUPERVISOR_BADGE = "<:mod_supervisor_badge:1437514356135821322>"
COMMUNICATION_SUPERVISOR_BADGE = "<:communication_supervisor_badge:1437514333763535068>"
MODERATOR_BADGE = "<:moderator_badge:1437514357230796891>"
COMMUNICATION_BADGE = "<:comunication_badge:1437514353304670268>"
SUPPORTAGENT_BADGE = "<:supportagent_badge:1437514361861177350>"

# User badges
PREMIUM_BADGE = "<:premium_badge:1437514360758075514>"
PARTNER_BADGE = "<:partener_badge:1437514359294263388>"
CONTRIBUTOR_BADGE = "<:contributor_badge:1437514354802036940>"
CERTIF_BADGE = "<:Certif_badge:1437514351774011392>"
BUGHUNTER_BADGE = "<:BugHunter_badge:1437514350406668318>"
BLACKLISTED_BADGE = "<:Blacklisted_badge:1437514349152571452>"

# =============================================================================
# DISCORD BADGES (user public flags)
# =============================================================================

DISCORD_STAFF = "<:discordstaff:1439636927321079890>"
DISCORD_PARTNER = "<:discordpartner:1439636926159126739>"
HYPESQUAD_EVENTS = "<:hypesquadevents:1439636933058760735>"
DISCORD_BUGHUNTER_1 = "<:discordbughunter1:1439636911999418589>"
DISCORD_BUGHUNTER_2 = "<:discordbughunter2:1439636913697853562>"
HYPESQUAD_BRAVERY = "<:hypesquadbravery:1439636930399572090>"
HYPESQUAD_BRILLIANCE = "<:hypesquadbrilliance:1439636931549069454>"
HYPESQUAD_BALANCE = "<:hypesquadbalance:1439636929195933706>"
DISCORD_EARLY_SUPPORTER = "<:discordearlysupporter:1439636915900125194>"
DISCORD_BOT_DEV = "<:discordbotdevcertif:1439636910845989005>"
ACTIVE_DEVELOPER = "<:activedeveloper:1439636908274618448>"
DISCORD_NITRO = "<:discordnitro:1439636918668099767>"
DISCORD_MOD = "<:discordmod:1439636917338509435>"
SUPPORTS_COMMANDS = "<:supportscommands:1439636938372944012>"


# =============================================================================
# CONVENIENCE DICTIONARIES
# Kept for backward compatibility and for code that iterates over emojis.
# All values reference the constants above.
# =============================================================================

EMOJIS = {
    # Status
    "done": DONE,
    "undone": UNDONE,
    "loading": LOADING,
    # Icons
    "settings": SETTINGS,
    "info": INFO,
    "warning": WARNING,
    "error": ERROR,
    # Actions
    "add": ADD,
    "edit": EDIT,
    # Bot
    "moddy": MODDY,
    "developer": DEV,
    "staff": STAFF,
    "ping": SUPPORT,
    # Extended
    "sync": SYNC,
    "user": USER,
    "dev": DEV,
    "blacklist": BLACKLIST,
    "time": TIME,
    "snowflake": SNOWFLAKE,
    "web": WEB,
    "history": HISTORY,
    "delete": DELETE,
    "commands": COMMANDS,
    "book": BOOK,
    "code": CODE,
    "bug": BUG,
    "logout": LOGOUT,
    "verified": VERIFIED,
    "verified_org": VERIFIED_ORG,
    "verified_org_member": VERIFIED_ORG_MEMBER,
    "next": NEXT,
    "back": BACK,
    "note": NOTE,
    "message": MESSAGE,
    "search": SEARCH,
    "save": SAVE,
    "reply": REPLY,
    "groups": GROUPS,
    "waving_hand": WAVING_HAND,
    "flag": FLAG,
    "toggle_off": TOGGLE_OFF,
    "toggle_on": TOGGLE_ON,
    "at": AT,
    "star": STAR,
    "required_fields": REQUIRED_FIELDS,
    "premium": PREMIUM,
    "support": SUPPORT,
    "balance": BALANCE,
    "download": DOWNLOAD,
    "emoji": EMOJI,
    "manage_user": MANAGE_USER,
    "banner": BANNER,
    "text": TEXT,
    "translate": TRANSLATE,
    "webhook": WEBHOOK,
    # Status dots
    "green_status": GREEN_STATUS,
    "yellow_status": YELLOW_STATUS,
    "red_status": RED_STATUS,
    # Staff badges
    "supportagent_badge": SUPPORTAGENT_BADGE,
    "moderator_badge": MODERATOR_BADGE,
    "mod_supervisor_badge": MOD_SUPERVISOR_BADGE,
    "comunication_badge": COMMUNICATION_BADGE,
    "support_supervisor_badge": SUPPORT_SUPERVISOR_BADGE,
    "supervisor_badge": SUPERVISOR_BADGE,
    "moddyteam_badge": MODDYTEAM_BADGE,
    "manager_badge": MANAGER_BADGE,
    "dev_badge": DEV_BADGE,
    "communication_supervisor_badge": COMMUNICATION_SUPERVISOR_BADGE,
    # Other badges
    "premium_badge": PREMIUM_BADGE,
    "partner_badge": PARTNER_BADGE,
    "contributor_badge": CONTRIBUTOR_BADGE,
    "certif_badge": CERTIF_BADGE,
    "bughunter_badge": BUGHUNTER_BADGE,
    "blacklisted_badge": BLACKLISTED_BADGE,
}

# Discord user flags -> emoji mapping (for /user command)
DISCORD_BADGES = {
    "staff": DISCORD_STAFF,
    "partner": DISCORD_PARTNER,
    "hypesquad": HYPESQUAD_EVENTS,
    "bug_hunter_level_1": DISCORD_BUGHUNTER_1,
    "bug_hunter_level_2": DISCORD_BUGHUNTER_2,
    "hypesquad_bravery": HYPESQUAD_BRAVERY,
    "hypesquad_brilliance": HYPESQUAD_BRILLIANCE,
    "hypesquad_balance": HYPESQUAD_BALANCE,
    "early_supporter": DISCORD_EARLY_SUPPORTER,
    "verified_bot_developer": DISCORD_BOT_DEV,
    "active_developer": ACTIVE_DEVELOPER,
    "nitro": DISCORD_NITRO,
    "discord_mod": DISCORD_MOD,
    "supports_commands": SUPPORTS_COMMANDS,
}

# Moddy attribute -> badge emoji mapping (for /user command)
MODDY_BADGES = {
    "PREMIUM": PREMIUM_BADGE,
    "PARTNER": PARTNER_BADGE,
    "CONTRIBUTOR": CONTRIBUTOR_BADGE,
    "CERTIF": CERTIF_BADGE,
    "BUGHUNTER": BUGHUNTER_BADGE,
    "BLACKLISTED": BLACKLISTED_BADGE,
    "DEVELOPER": DEV_BADGE,
    "MODDYTEAM": MODDYTEAM_BADGE,
    "MANAGER": MANAGER_BADGE,
    "SUPERVISOR": SUPERVISOR_BADGE,
    "SUPPORT_SUPERVISOR": SUPPORT_SUPERVISOR_BADGE,
    "COMMUNICATION_SUPERVISOR": COMMUNICATION_SUPERVISOR_BADGE,
    "MOD_SUPERVISOR": MOD_SUPERVISOR_BADGE,
    "MODERATOR": MODERATOR_BADGE,
    "COMMUNICATION": COMMUNICATION_BADGE,
    "SUPPORTAGENT": SUPPORTAGENT_BADGE,
}

# Auto-assigned badges based on attributes
AUTO_MODDY_BADGES = {
    "TEAM": MODDYTEAM_BADGE,
    "SUPPORT": SUPPORTAGENT_BADGE,
    "VERIFIED": CERTIF_BADGE,
}

# Sanction type emojis (moderation cases)
SANCTION_EMOJIS = {
    "INTERSERVER_WARN": "\u26a0\ufe0f",
    "INTERSERVER_TIMEOUT": "\u23f1\ufe0f",
    "INTERSERVER_BLACKLIST": "\U0001f6ab",
    "GLOBAL_WARN": "\u26a0\ufe0f",
    "GLOBAL_LIMITED": "\u26d4",
    "GLOBAL_BLACKLIST": "\U0001f6ab",
}
SANCTION_EMOJI_DEFAULT = "\U0001f4cb"


# =============================================================================
# VERIFICATION BADGE UTILITIES
# =============================================================================

DOCS_VERIFIED_URL = "https://docs.moddy.app/articles/verified-badges"


def format_verification_badge(badge: str) -> str:
    """Wrap a verification badge emoji in a hyperlink to the docs page.

    Returns an empty string if badge is empty.
    """
    if not badge:
        return ""
    return f"[{badge}]({DOCS_VERIFIED_URL})"


def _parse_org_list(raw) -> list:
    """Parse VERIFIED_ORG_MEMBER_ORG — supports JSON array or legacy plain string."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    s = str(raw).strip()
    if s.startswith("["):
        import json as _json
        try:
            return _json.loads(s)
        except Exception:
            pass
    return [s]


def get_user_verification_badge(user_data: dict, moddy_attributes: dict, user_verification_data: dict = None) -> tuple:
    """Determine the verification badge and org affiliation for a user.

    Priority:
      1. VERIFIED_ORG attribute → verified_org badge  (tier "verified_org")
      2. Discord staff flag / TEAM attribute / VERIFIED_ORG_MEMBER attribute
         → verified badge  (tier "org_member")
      3. VERIFIED attribute → verified badge  (tier "verified")

    Args:
        user_data: Raw Discord API user dict (needs public_flags).
        moddy_attributes: User's attributes dict from DB (boolean flags).
        user_verification_data: Optional dict from DB data.verification — holds
            dates and org lists. Falls back to legacy attribute keys when absent.

    Returns:
        (badge_emoji: str, org_names: list[str], tier: str | None)
        badge_emoji is an empty string when the user has no badge.
        tier is one of "verified_org" | "org_member" | "verified" | None.
    """
    # 1. Verified organisation
    if moddy_attributes.get("VERIFIED_ORG"):
        return (VERIFIED_ORG, [], "verified_org")

    # 2. Org-member badge (auto or manual)
    public_flags = user_data.get("public_flags", 0)
    is_discord_staff = bool(public_flags & (1 << 0))
    is_moddy_team = bool(moddy_attributes.get("TEAM"))
    is_org_member_attr = bool(moddy_attributes.get("VERIFIED_ORG_MEMBER"))

    if is_discord_staff or is_moddy_team or is_org_member_attr:
        orgs = []
        if is_discord_staff:
            orgs.append("Discord")
        if is_moddy_team:
            orgs.append("Moddy Team")
        if is_org_member_attr:
            if user_verification_data is not None:
                raw = (user_verification_data.get("VERIFIED_ORG_MEMBER") or {}).get("orgs") or []
                custom_orgs = raw if isinstance(raw, list) else _parse_org_list(raw)
            else:
                # Backward compat: orgs stored in attributes
                custom_orgs = _parse_org_list(moddy_attributes.get("VERIFIED_ORG_MEMBER_ORG"))
            for custom_org in custom_orgs:
                if custom_org not in orgs:
                    orgs.append(custom_org)
        return (VERIFIED_ORG_MEMBER, orgs, "org_member")

    # 3. Normal verified
    if moddy_attributes.get("VERIFIED"):
        return (VERIFIED, [], "verified")

    return ("", [], None)
