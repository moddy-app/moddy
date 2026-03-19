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
ERROR = "<:error:1444049460924776478>"
WARNING = "<:warning:1446108410092195902>"
INFO = "<:info:1401614681440784477>"
LOADING = "<a:loading:1455219844080336907>"

# Status dots
GREEN_STATUS = "<:green_status:1450929035428495505>"
YELLOW_STATUS = "<:yellow_status:1450929037542166669>"
RED_STATUS = "<:red_status:1450929038758772940>"

# Actions
ADD = "<:add:1439697866049323090>"
REMOVE = "<:remove:1398729478435393598>"
EDIT = "<:edit:1401600709824086169>"
DELETE = "<:delete:1401600770431909939>"
SAVE = "<save:1444101502154182778>"
SEARCH = "<search:1443752796460552232>"
SYNC = "<:sync:1398729150885269546>"
BACK = "<:back:1401600847733067806>"
NEXT = "<next:1443745574972031067>"
LOGOUT = "<:logout:1401603690858676224>"
DOWNLOAD = "<:download:1401600503867248730>"
REPLY = "<reply:1444821779444138146>"

# Navigation / UI
SETTINGS = "<:settings:1398729549323440208>"
COMMANDS = "<:commands:1401610449136648283>"
TOGGLE_ON = "<:toogle_on:1446267419034386473>"
TOGGLE_OFF = "<:toggle_off:1446267399786594514>"
REQUIRED_FIELDS = "<:required_fields:1446549185385074769>"

# Objects / Concepts
USER = "<:user:1398729712204779571>"
DEV = "<:dev:1398729645557285066>"
MODDY = "<:moddy:1396880909117947924>"
MODDY_ALT = "<:moddy:1451280939412881508>"
BLACKLIST = "<:blacklist:1401596866478477363>"
TIME = "<:time:1398729780723060736>"
SNOWFLAKE = "<:snowflake:1398729841938792458>"
WEB = "<:web:1398729801061240883>"
HISTORY = "<:history:1401600464587456512>"
BOOK = "<:book:1446557736350388364>"
CODE = "<:code:1401610523803652196>"
BUG = "<:bug:1401614189482475551>"
VERIFIED = "<:verified:1398729677601902635>"
MINI_VERIFIED = "<:miniverified:1439667456737280021>"
NOTE = "<note:1443749708857085982>"
MESSAGE = "<message:1443749710073696286>"
GROUPS = "<:groups:1446127489842806967>"
WAVING_HAND = "<:waving_hand:1446127491004760184>"
FLAG = "<:flag:1446197210198048778>"
AT = "<:at:1446199071013470319>"
STAR = "<:star:1446267438671859832>"
EMOJI = "<:emoji:1398729407065100359>"
PREMIUM = "<:premium:1401602724801548381>"
SUPPORT = "<:support:1398734366670065726>"
BALANCE = "<:balance:1398729232862941445>"
MANAGE_USER = "<:manageuser:1398729745293774919>"
BANNER = "<:banner:1439659080472989726>"
TEXT = "<:text:1439692405317046372>"
TRANSLATE = "<:translate:1398720130950627600>"
WEBHOOK = "<:webhook:1438636058660045041>"
STAFF = "<:staff:1398729432759476245>"

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
    "remove": REMOVE,
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
