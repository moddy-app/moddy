"""
Centralized emoji registry for Moddy.
All custom Discord emojis are defined here.
To change an emoji, update it in this file — it will propagate everywhere.
"""
import re
from typing import Any, Dict, Tuple

# =============================================================================
# CORE UI EMOJIS
# =============================================================================

# Status indicators
DONE = "<:done:1519800188925902881>"
UNDONE = "<:undone:1519800313324896327>"
ERROR = "<:error:1519790252594827264>"
WARNING = "<:warning:1519789903100121139>"
EXCLAMATION = "<:exclamation:1536767802139082762>"  # global sanction notices
INFO = "<:info:1519793991045091388>"
LOADING = "<a:spinner:1534857169667883078>"
# Animated "the bot is working on it" mascot — used for AI processing states
# (voice transcription), where the plain spinner reads as a generic loader.
ROBOT_WORKING = "<a:Robot:1535676980605878312>"

# Status dots
GREEN_STATUS = "<:green_status:1450929035428495505>"
YELLOW_STATUS = "<:yellow_status:1450929037542166669>"
RED_STATUS = "<:red_status:1450929038758772940>"

# Actions
ADD = "<:add:1519791773235413022>"
EDIT = "<:edit:1519795936568676383>"
DELETE = "<:delete:1519795753164210447>"
SAVE = "<:save:1519792503346434048>"
SEARCH = "<:search:1519790418290675822>"
SYNC = "<:sync:1519802581893775383>"
BACK = "<:back:1519795556665397431>"
NEXT = "<:next:1519791619526754354>"
PAUSE = "<:pause:1519787332801269811>"
PLAY = "<:play:1519787501789773996>"
LABS = "<:labs:1560741865915949107>"  # Simulation state (module state, automod dry run)
LOGOUT = "<:logout:1519795093605974098>"
DOWNLOAD = "<:download:1519796568255889578>"
REPLY = "<:reply:1519790151130415154>"

# Navigation / UI
SETTINGS = "<:settings:1519800032499339354>"
COMMANDS = "<:commands:1519794878933106830>"
TOGGLE_ON = "<:toogle_on:1541216663377289226>"
TOGGLE_OFF = "<:toogle_off:1541216665688342588>"
REQUIRED_FIELDS = "<:required_fields:1446549185385074769>"

# Objects / Concepts
USER = "<:user:1519798911517196511>"
DEV = "<:dev:1398729645557285066>"
MODDY = "<:ModdyIcon:1517239367184285909>"
MODDY_SQUARE = "<:ModdyIconCarre:1517239121322578090>"  # rounded-square variant
# Smaller rounded-square variant — the one to use when the logo sits inline in
# a line of text or as a title icon, where the full-size mark reads as oversized.
MODDY_SQUARE_MIN = "<:ModdyIconCarreMin:1517240474916880556>"
MODDY_ALT = "<:moddy:1451280939412881508>"
BLACKLIST = "<:blacklist:1519797375470669944>"
TIME = "<:time:1519798202990330087>"
SNOWFLAKE = "<:snowflake:1519797680069541999>"
WEB = "<:web:1519798069191901254>"
HISTORY = "<:history:1519796822963392755>"
BOOK = "<:book:1519788969691316467>"
CODE = "<:code:1519794490750271641>"
BUG = "<:bug:1519794242296483952>"
BUILD = "<:build:1542311000479498310>"  # configuration help ("we set it up for you")
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
EMOJI = "<:emoji:1519800926636867675>"
PREMIUM = "<:premium:1519795224493424893>"
# Module icons (the /config list and each module's config screen title)
FINGERPRINT = "<:fingerprint:1558791867988971620>"  # AltGuard
NEWSPAPER = "<:newspaper:1558791863228567603>"      # Server logs
ASSIGNMENT = "<:assignment:1558791861802639400>"    # Member applications
TIMER = "<:timer:1558791855297269880>"              # Adaptive slowmode
REFRESH = "<:refresh:1558791853745250314>"          # Auto restore roles
DOLLARS = "<:dollars:1543645900797116436>"  # billing: Stripe invoice receipts
SUPPORT = "<:support:1519797524813185164>"
BALANCE = "<:balance:1519801728961351730>"
MANAGE_USER = "<:manageuser:1519798563880698066>"
LEGAL = "<:legal:1519801263397539890>"
MIC_OFF = "<:mic_off:1520555496665255936>"
VOICE_CHAT = "<:voice_chat:1535700758601666672>"  # voice transcription module
AI_SHIELD = "<:ai_shield:1561544570809098342>"  # AI detector module (C2PA) — no _dark twin yet
CHECK = "<:check_100dp_FFFFFF_FILL1_wght700:1519751484517122088>"
BANNER = "<:banner:1519792732716007634>"
TEXT = "<:text:1519791921462120601>"
TRANSLATE = "<:translate:1519802660856008834>"
WEBHOOK = "<:webhook:1519793325329219675>"
# The `:staff:` artwork was deleted; the Moddy Team badge is the same idea
# and is defined further down. Aliased rather than re-pointed at each call
# site so a future dedicated icon is a one-line change again.
STAFF = "<:moddyteam_badge:1437514344467398837>"
FILTER = "<:filter:1520586655180783666>"
FOLDER = "<:folder:1520590405043880107>"
FOLDERS = "<:folders:1520590403533934713>"
IMAGE = "<:image:1520593192892764260>"
SHIELD = "<:shield:1521471376815292498>"   # automod icon
HAND = "<:hand:1521517865348632706>"       # appeal "claim" button
LINK = "<:link:1521517863607734385>"       # appeal "invite" button
PENDING = "<:pending:1521282587962900611>"  # hourglass: appeal pending, automod moderator approval pending
ROCKET = "<a:Rocket:1535783839870353499>"  # bot customization bio attribution
ROCKET_LAUNCH = "<:rocket_launch:1545028494298447873>"  # Bump Reminder module
SHAPES = "<:shapes:1552314638782300260>"  # Member Applications module
ALTGUARD = "<a:DisguisedFace:1539405255576657981>"  # AltGuard verification panel message
CLAIM = "<:claim:1541239118888181770>"      # take a ticket in charge
THREADS = "<:threads:1541216661699436675>"  # private staff thread

# =============================================================================
# UTILITY TOOLS (/define, /hash, /whois, /image, /music, ...)
# -----------------------------------------------------------------------------
# Icons already uploaded are real tokens. The others are EMPTY until their
# artwork (white + `_dark` twin) is uploaded: every tool card renders an empty
# icon as "no icon" (utils/tool_card.py), so filling the id here is the only
# change needed once it exists. Never point one of these at an unrelated icon.
# =============================================================================
COLOR = "<:color:1519800727231266858>"              # /color
LOCK = "<:lock:1519799617087213779>"                # /ssl (certificate)
DATA_OBJECT = "<:data_object:1519795407453159474>"  # JSON blocks (/jwt)
DICTIONARY = ""      # /define — Material `dictionary`
REGEX = ""           # /regex — Material `regular_expression`
TAG = ""             # /hash — Material `tag`
DIFFERENCE = ""      # /diff — Material `difference`
ANALYTICS = ""       # /textstats — Material `analytics`
PASSWORD = ""        # /password — Material `password`
NUMBERS = ""         # /uuid — Material `123`
TOKEN = ""           # /jwt — Material `token`
GPP_MAYBE = ""       # /pwned — Material `gpp_maybe`
MONITOR_HEART = ""   # /status — Material `monitor_heart`
BLUE_STATUS = ""     # /status: maintenance (same style as green_status)
GREY_STATUS = ""     # /status: unknown (same style as green_status)
BUSINESS = ""        # /company — Material `business`
DNS = ""             # /dns — Material `dns`
ACCOUNT_TREE = ""    # /subdomains — Material `account_tree`
ALT_ROUTE = ""       # /link-check redirect chain — Material `alt_route`
PERSON_SEARCH = ""   # /username — Material `person_search`
ASPECT_RATIO = ""    # /image resize — Material `aspect_ratio`
COMPRESS = ""        # /image compress — Material `compress`
TRANSFORM = ""       # /convert — Material `transform`
AUTO_FIX_HIGH = ""   # /image remove background — Material `auto_fix_high`
FRAME_INSPECT = ""   # /exif — Material `frame_inspect`
LOCATION_ON = ""     # /exif GPS — Material `location_on`
IMAGE_SEARCH = ""    # /sauce — Material `image_search`
MOVIE = ""           # /sauce anime result — Material `movie`
MUSIC_NOTE = ""      # /music + music links module — Material `music_note`
# Service logos — shown next to the service name in each tool's footer phrase
# ("Provided by <logo> **X**", utils/tool_card.source_line) and on
# link buttons. Coloured brand artwork is visible on both themes: no `_dark`
# twin needed unless the logo itself is white/black.
SPOTIFY = ""
DEEZER = ""
APPLE_MUSIC = ""
YOUTUBE_MUSIC = ""
WIKTIONARY = ""      # /define
HIBP = ""            # /pwned — Have I Been Pwned
OPENAI = ""          # /tldr
DEEPL = ""           # translate button
DATA_GOUV = ""       # /company — Annuaire des Entreprises / data.gouv
CLOUDFLARE = ""      # /dns (DNS-over-HTTPS) + /status
CRTSH = ""           # /subdomains — crt.sh (Certificate Transparency)
VIRUSTOTAL = ""      # /link-check
URLSCAN = ""         # /link-check
WHATSMYNAME = ""     # /username
SAUCENAO = ""        # /sauce
TRACEMOE = ""        # /sauce
ANILIST = ""         # /sauce anime link
GOOGLE_LENS = ""     # /sauce link button
YANDEX = ""          # /sauce link button
BING = ""            # /sauce link button
TINEYE = ""          # /sauce link button
CLOUDCONVERT = ""    # /convert (office documents, ebooks)
OPENSTREETMAP = ""   # /exif GPS map button
# /status services (one logo each)
DISCORD_LOGO = ""
GITHUB = ""
REDDIT = ""
DROPBOX = ""
ATLASSIAN = ""
VERCEL = ""
NETLIFY = ""
ZOOM = ""
# Added by the lookup tools (/status): one logo each, empty until uploaded.
ANTHROPIC = ""       # /status — Claude (Anthropic) logo
EPIC_GAMES = ""      # /status — Epic Games logo
NPM = ""             # /status — npm logo
DIGITALOCEAN = ""    # /status — DigitalOcean logo
FIGMA = ""           # /status — Figma logo
# Twitch reuses TWITCH (social notifications) and OpenAI reuses OPENAI above.

# =============================================================================
# TICKETS MODULE
# -----------------------------------------------------------------------------
# Everything in the Tickets module references these constants, so replacing an
# id here propagates to the panels, the ticket control bar, the slash commands
# and the /config screens at once. The ones still marked (placeholder) are
# ALIASES onto existing emojis; replace them with dedicated artwork when it
# exists — that is the only change needed.
# =============================================================================
TICKET = "<:ticket:1541216667420594257>"   # module icon + ticket cards
TICKET_STAFF_THREAD = THREADS              # private staff thread
TICKET_CLOSE = UNDONE                      # close a ticket (a plain cross)
TICKET_PARTICIPANTS = MANAGE_USER          # manage who is in the ticket
TICKET_CLAIM = CLAIM                       # claim / release a ticket
TICKET_PANEL = NOTE             # a ticket panel message          (placeholder)
TICKET_CATEGORY = FOLDER        # a ticket category               (placeholder)
TICKET_CLOSE_REQUEST = HAND     # ask the staff to close          (placeholder)
TICKET_ESCALATE = SHIELD        # escalate to the responsibles    (placeholder)
TICKET_RENAME = EDIT            # rename the ticket               (placeholder)
TICKET_MOVE = FOLDERS           # move to another category        (placeholder)
TICKET_PERMISSIONS = LEGAL      # per-role ticket permissions     (placeholder)
TICKET_ACCESS = MANAGE_USER     # who may open a ticket           (placeholder)
TICKET_REOPEN = SYNC            # reopen a closed ticket          (placeholder)

# =============================================================================
# SOCIAL PLATFORMS (Social Notifications module)
# -----------------------------------------------------------------------------
# NOTE: These are PLACEHOLDER ids — replace each one with the real custom emoji
# for the platform. Everything in the codebase references these constants, so
# updating the id here propagates everywhere. Also update PLATFORM_EMOJIS below
# stays in sync automatically (it references the constants).
# =============================================================================
SOCIAL = "<:web:1519798069191901254>"           # generic "social notifications" icon (placeholder — no custom emoji yet)
YOUTUBE = "<:youtube:1515511923066671185>"
TWITCH = "<:twitch:1515511921938399343>"
BLUESKY = "<:bluesky:1515511920235516024>"
RSS = "<:rss:1519788506980028477>"
INSTAGRAM = "<:web:1519798069191901254>"         # TODO replace with <:instagram:...> (platform is future/disabled)

# =============================================================================
# BUMP REMINDER — directory icons
# =============================================================================
# One per supported server directory. bumpreminder/registry.py reads these
# (BumpBot.emoji), so the cards, the /config panel and this file can never
# disagree on a directory's icon.
DISBOARD = "<:disboard:1545025221612802101>"
DSMONITORING = "<:DSMonitoring:1545027259323256842>"
DINVITES = "<:dinvites:1545025725948624956>"
DISCORDL = "<:DiscordDL:1545024517066465370>"
BEEMP = "<:beemp:1545026898520969216>"
DISCORDTOP = "<:DTOP:1545026416331198515>"
FRENCHGG = "<:frenchgg:1545026009235988490>"

# directory key (BumpBot.key) -> emoji
BUMP_DIRECTORY_EMOJIS = {
    "disboard": DISBOARD,
    "dsmonitoring": DSMONITORING,
    "dinvites": DINVITES,
    "dl": DISCORDL,
    "beemp": BEEMP,
    "dtop": DISCORDTOP,
    "frenchgg": FRENCHGG,
}

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
    "labs": LABS,
    "done": DONE,
    "undone": UNDONE,
    "loading": LOADING,
    "robot_working": ROBOT_WORKING,
    # Icons
    "settings": SETTINGS,
    "info": INFO,
    "warning": WARNING,
    "exclamation": EXCLAMATION,
    "error": ERROR,
    # Actions
    "add": ADD,
    "edit": EDIT,
    # Bot
    "moddy": MODDY,
    "moddy_square": MODDY_SQUARE,
    "moddy_square_min": MODDY_SQUARE_MIN,
    "developer": DEV,
    "staff": STAFF,
    "ping": SUPPORT,
    # Extended
    "sync": SYNC,
    "user": USER,
    "dev": DEV,
    "blacklist": BLACKLIST,
    "time": TIME,
    "rocket_launch": ROCKET_LAUNCH,
    "shapes": SHAPES,
    "snowflake": SNOWFLAKE,
    "web": WEB,
    "history": HISTORY,
    "delete": DELETE,
    "commands": COMMANDS,
    "book": BOOK,
    "code": CODE,
    "bug": BUG,
    "build": BUILD,
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
    "claim": CLAIM,
    "threads": THREADS,
    "ticket": TICKET,
    "banner": BANNER,
    "text": TEXT,
    "translate": TRANSLATE,
    "webhook": WEBHOOK,
    "voice_chat": VOICE_CHAT,
    "ai_shield": AI_SHIELD,
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

# Sanction action emojis (moderation cases). Keyed by SanctionAction value.
SANCTION_ACTION_EMOJIS = {
    "warn": WARNING,
    "mute": TIME,
    "ban": LEGAL,
    "restrict": UNDONE,
    "revoke_access": DELETE,
}
SANCTION_ACTION_EMOJI_DEFAULT = NOTE

# Sanction → title icon for member-facing DMs / server logs. Matches the manual
# sanction DMs (cogs/moderation_commands.py) so automod looks identical.
SANCTION_DM_EMOJIS = {
    "warn": WARNING,
    "mute": MIC_OFF,
    "ban": LEGAL,
    "restrict": UNDONE,
    "revoke_access": DELETE,
}

# Sanction → accent colour for DM / log panels (same palette as manual
# sanctions): orange for a warn, red for the punitive actions.
SANCTION_ACCENT_COLORS = {
    "warn": 0xF28500,
    "mute": 0xDA3E27,
    "ban": 0xDA3E27,
    "restrict": 0xF28500,
    "revoke_access": 0xDA3E27,
}
SANCTION_ACCENT_DEFAULT = 0xF28500


def get_sanction_dm_emoji(action: str) -> str:
    """Title icon for a sanction action in a member-facing message."""
    return SANCTION_DM_EMOJIS.get(action or "", WARNING)


def get_sanction_accent(action: str) -> int:
    """Accent colour (int) for a sanction action in a DM / log panel."""
    return SANCTION_ACCENT_COLORS.get(action or "", SANCTION_ACCENT_DEFAULT)

# Case type emojis (moderation cases). Keyed by CaseType value.
CASE_TYPE_EMOJIS = {
    "global": MODDY,
    "network": WEB,
    "guild": "",
    "external": WEB,
}
CASE_TYPE_EMOJI_DEFAULT = INFO


# =============================================================================
# STANDARD (DISCORD-PROVIDED) UNICODE EMOJI VALIDATION
# =============================================================================
# Used by modules (e.g. Starboard) that must only accept reactions/config
# values among the Unicode emojis Discord itself provides — never a custom
# guild emoji. `discord.PartialEmoji.is_unicode_emoji()` only tells us the
# value isn't a `<:name:id>` custom emoji, it doesn't verify the string is
# an actual emoji, so we range-check the codepoints ourselves.

_STANDARD_EMOJI_UNICODE_RANGES = (
    (0x1F300, 0x1FAFF),  # Misc pictographs, emoticons, transport, symbols & pictographs extended-A
    (0x2600, 0x27BF),    # Misc symbols & dingbats
    (0x2300, 0x23FF),    # Misc technical (⌚ ⏰ ⏳ …)
    (0x2B00, 0x2BFF),    # Misc symbols & arrows (⭐ ⚡ ➡️ …)
    (0x2190, 0x21FF),    # Arrows
    (0x1F1E6, 0x1F1FF),  # Regional indicator symbols (used in flag sequences)
)


def _is_standard_emoji_codepoint(codepoint: int) -> bool:
    return any(low <= codepoint <= high for low, high in _STANDARD_EMOJI_UNICODE_RANGES)


def is_standard_discord_emoji(value: str) -> bool:
    """Check that `value` is a single Discord-standard Unicode emoji.

    Rejects custom/guild emojis (``<:name:id>``) and arbitrary text. Handles
    the variation selector (``️``), flag sequences (pairs of regional
    indicators), keycap sequences (``#``/``*``/digit + combining keycap) and
    ZWJ sequences (e.g. family/profession emojis).
    """
    if not value:
        return False

    stripped = value.replace('️', '')  # variation selector-16

    # Keycap sequence: 0-9 / # / * + combining enclosing keycap (U+20E3)
    if len(stripped) == 2 and stripped[1] == '⃣' and (stripped[0].isdigit() or stripped[0] in '#*'):
        return True

    # Flag sequence: pair of regional indicator symbols
    if len(stripped) == 2 and all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in stripped):
        return True

    # Simple emoji or ZWJ sequence (ZWJ = U+200D, e.g. family/profession emojis)
    for part in stripped.split('‍'):
        if len(part) != 1 or not _is_standard_emoji_codepoint(ord(part)):
            return False
    return True


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


# =============================================================================
# SERVER LOGS EMOJI SET
# -----------------------------------------------------------------------------
# The icon set of the advanced server logs (see docs/features/LOGS.md). It is kept as a
# dictionary rather than 158 module constants for two reasons: the names come
# from Discord's own vocabulary (``createchannel``, ``timedout``, ``Notchecked``)
# and several of them would collide with the general constants above, and the
# logs resolve their icon *by name* from the registry.
#
# This set is the **only** icon source allowed in the logs configuration panel
# and in the log messages themselves — the sole exception being the module icon
# shown in the /config picker.
#
# Product-approved exception (the other direction): the Automod AI /config panel
# (modules/configs/automod_ai_*.py) uses this set for its SANCTION ACTION icons
# only — delete message ("messageremove"), warn ("Warn"), timeout ("timeout"),
# kick ("kick") and ban ("ban"). Everything else in that panel keeps the regular
# constants above.
# =============================================================================

LOG_EMOJIS = {
    "Verificationlevellow":           "<:Verificationlevellow:1540889999476527166>",
    "Viewrules":                      "<:Viewrules:1540890000848326706>",
    "viewserverasrole":               "<:viewserverasrole:1540890001997307994>",
    "view":                           "<:view:1540890003045879818>",
    "Warn":                           "<:Warn:1540890004128272434>",
    "warnsyellow":                    "<:warnsyellow:1540890005403078666>",
    "warns":                          "<:warns:1540890006263169096>",
    "webhookcreate":                  "<:webhookcreate:1540890007743500361>",
    "webhookremove":                  "<:webhookremove:1540890008825630730>",
    "webhookupdate":                  "<:webhookupdate:1540890009974870097>",
    "Webhooks":                       "<:Webhooks:1540890011342213232>",
    "Rules":                          "<:Rules:1540890012655296602>",
    "sendalert":                      "<:sendalert:1540890013863252088>",
    "serverguide":                    "<:serverguide:1540890015045779596>",
    "serverstore":                    "<:serverstore:1540890016060805231>",
    "slash":                          "<:slash:1540890017205854298>",
    "sort":                           "<:sort:1540890018346962974>",
    "soundboardadd":                  "<:soundboardadd:1540890019470901348>",
    "soundboardremove":               "<:soundboardremove:1540890020800495616>",
    "soundboardupdate":               "<:soundboardupdate:1540890021941219482>",
    "spamactivity":                   "<:spamactivity:1540890023229132952>",
    "stagecreated":                   "<:stagecreated:1540890024478904380>",
    "stageremoved":                   "<:stageremoved:1540890026131587192>",
    "stageupdated":                   "<:stageupdated:1540890027335360584>",
    "stickerscreated":                "<:stickerscreated:1540890029034045511>",
    "stickersdeleted":                "<:stickersdeleted:1540890030250270720>",
    "stickersupdated":                "<:stickersupdated:1540890031357693963>",
    "Stop":                           "<:Stop:1540890032481505390>",
    "sub":                            "<:sub:1540890033832075354>",
    "timedout":                       "<:timedout:1540890034792693801>",
    "timeout":                        "<:timeout:1540890036277485598>",
    "timeout1":                       "<:timeout1:1540890037858865253>",
    "unusualdmactivity":              "<:unusualdmactivity:1540890038991323146>",
    "updatechannel":                  "<:updatechannel:1540890040773910589>",
    "updatehome":                     "<:updatehome:1540890043994873927>",
    "updatemember":                   "<:updatemember:1540890045140176917>",
    "updaterole":                     "<:updaterole:1540890046297542737>",
    "updateserver":                   "<:updateserver:1540890047442591836>",
    "updatethread":                   "<:updatethread:1540890048449224746>",
    "Uploadfile":                     "<:Uploadfile:1540890049892188330>",
    "uploadimage":                    "<:uploadimage:1540890051049820232>",
    "Verificationlevelhighest":       "<:Verificationlevelhighest:1540890052207312947>",
    "raid":                           "<:raid:1540890053470064750>",
    "removehome":                     "<:removehome:1540890054476570684>",
    "removemember":                   "<:removemember:1540890055680327700>",
    "removerole":                     "<:removerole:1540890056665866321>",
    "removethread":                   "<:removethread:1540890057584549939>",
    "removeuserfromrole":             "<:removeuserfromrole:1540890059258069012>",
    "right":                          "<:right:1540890060587667456>",
    "roleicon":                       "<:roleicon:1540890061833375884>",
    "roleicon1":                      "<:roleicon1:1540890063066505316>",
    "roleicon2":                      "<:roleicon2:1540890064903733389>",
    "roleicon3":                      "<:roleicon3:1540890067697012856>",
    "roleicon4":                      "<:roleicon4:1540890068925948014>",
    "roleicon5":                      "<:roleicon5:1540890070129836124>",
    "roleicon6":                      "<:roleicon6:1540890071618822194>",
    "roleicon7":                      "<:roleicon7:1540890072759406704>",
    "roleicon8":                      "<:roleicon8:1540890073988603944>",
    "roleicon9":                      "<:roleicon9:1540890075154612366>",
    "roleicon10":                     "<:roleicon10:1540890076425486407>",
    "roleicon11":                     "<:roleicon11:1540890077767667722>",
    "roleicon12":                     "<:roleicon12:1540890079449452696>",
    "roleicon13":                     "<:roleicon13:1540890080812601464>",
    "roleicon14":                     "<:roleicon14:1540890081903251548>",
    "roleicon15":                     "<:roleicon15:1540890083110948934>",
    "roleicon16":                     "<:roleicon16:1540890084113645719>",
    "roleicon17":                     "<:roleicon17:1540890085736579173>",
    "roleicon18":                     "<:roleicon18:1540890086911123526>",
    "ID":                             "<:ID:1540890092183228456>",
    "invitecreate":                   "<:invitecreate:1540890093592649848>",
    "inviteremove":                   "<:inviteremove:1540890094729302149>",
    "inviteupdate":                   "<:inviteupdate:1540890096180396163>",
    "joinmethod":                     "<:joinmethod:1540890097434492978>",
    "key":                            "<:key:1540890098743386163>",
    "kick":                           "<:kick:1540890100806721576>",
    "links":                          "<:links:1540890102010744912>",
    "locked":                         "<:locked:1540890103218442240>",
    "locked1":                        "<:locked1:1540890104313421925>",
    "media":                          "<:media:1540890105424777257>",
    "messageremove":                  "<:messageremove:1540890106993311756>",
    "modview":                        "<:modview:1540890109245661194>",
    "moreoptions":                    "<:moreoptions:1540890110529372241>",
    "newtier":                        "<:newtier:1540890111720554526>",
    "nodiscovery":                    "<:nodiscovery:1540890112982781993>",
    "Notchecked":                     "<:Notchecked:1540890114257981600>",
    "owner":                          "<:owner:1540890115910402088>",
    "passedverification":             "<:passedverification:1540890117127012362>",
    "permissions":                    "<:permissions:1540890118670389298>",
    "pickcolor":                      "<:pickcolor:1540890120050319501>",
    "Play":                           "<:Play:1540890121396682863>",
    "premiumbadge1":                  "<:premiumbadge1:1540890122512371743>",
    "premiumbadge3":                  "<:premiumbadge3:1540890124009607238>",
    "premiumbadge":                   "<:premiumbadge:1540890125083344927>",
    "Preventmentionspam":             "<:Preventmentionspam:1540890126467473459>",
    "Products":                       "<:Products:1540890128279535616>",
    "quarantined":                    "<:quarantined:1540890129676243055>",
    "questions":                      "<:questions:1540890130854838272>",
    "deny":                           "<:deny:1540890131995562044>",
    "discordjoin":                    "<:discordjoin:1540890133228683264>",
    "dm":                             "<:dm:1540890134524731484>",
    "donthave":                       "<:donthave:1540890135707648020>",
    "dragquestions":                  "<:dragquestions:1540890144746381442>",
    "dragrole":                       "<:dragrole:1540890146042290226>",
    "dragrule":                       "<:dragrule:1540890147405561886>",
    "editrole":                       "<:editrole:1540890148584165538>",
    "Edit":                           "<:Edit:1540890149741793421>",
    "Emojicreate":                    "<:Emojicreate:1540890150869930025>",
    "Emojiremove":                    "<:Emojiremove:1540890152128348290>",
    "Emojiupdate":                    "<:Emojiupdate:1540890153302888478>",
    "emojisslots":                    "<:emojisslots:1540890154493808680>",
    "enablementionraiddetection":     "<:enablementionraiddetection:1540890155714617384>",
    "eventcreated":                   "<:eventcreated:1540890156838690847>",
    "eventdeleted":                   "<:eventdeleted:1540890158130397284>",
    "eventupdated":                   "<:eventupdated:1540890159367585893>",
    "featurecreated":                 "<:featurecreated:1540890160663765062>",
    "featureremoved":                 "<:featureremoved:1540890161842491442>",
    "Filter":                         "<:Filter:1540890162983338166>",
    "grantedperm":                    "<:grantedperm:1540890164195496026>",
    "have":                           "<:have:1540890165872959608>",
    "boostlevel2":                    "<:boostlevel2:1540890166963605564>",
    "boostlevel3":                    "<:boostlevel3:1540890168096067625>",
    "boostlevelonly":                 "<:boostlevelonly:1540890169538777198>",
    "bot":                            "<:bot:1540890170793140364>",
    "Channelsfollowed":               "<:Channelsfollowed:1540890172009480242>",
    "channls":                        "<:channls:1540890173229768825>",
    "check":                          "<:check:1540890174253436968>",
    "Checked":                        "<:Checked:1540890175410937857>",
    "close":                          "<:close:1540890176681672734>",
    "commandpremissionupdated":       "<:commandpremissionupdated:1540890178078380042>",
    "connectioncreated":              "<:connectioncreated:1540890179303383081>",
    "connectiondeleted":              "<:connectiondeleted:1540890180486168626>",
    "connectionupdated":              "<:connectionupdated:1540890181660450886>",
    "context":                        "<:context:1540890183040503918>",
    "createchannel":                  "<:createchannel:1540890184449527888>",
    "createhome":                     "<:createhome:1540890185808748664>",
    "createrole":                     "<:createrole:1540890187046068304>",
    "createthread":                   "<:createthread:1540890188480258078>",
    "defaultpermissions":             "<:defaultpermissions:1540890189608652800>",
    "deletechannel":                  "<:deletechannel:1540890190954893342>",
    "left":                           "<:left:1540890193094119524>",
    "accept":                         "<:accept:1540890194704867348>",
    "accept1":                        "<:accept1:1540890195874816140>",
    "addicon":                        "<:addicon:1540890196936101888>",
    "addmember":                      "<:addmember:1540890198538199183>",
    "addrole":                        "<:addrole:1540890200551587931>",
    "addrole1":                       "<:addrole1:1540890201851830365>",
    "addrole2":                       "<:addrole2:1540890202959126638>",
    "addrule":                        "<:addrule:1540890204330524692>",
    "addview":                        "<:addview:1540890205609922600>",
    "added":                          "<:added:1540890206691921980>",
    "Alllogs":                        "<:Alllogs:1540890207518203915>",
    "ban":                            "<:ban:1540890210240569475>",
    "Blockcommonly":                  "<:Blockcommonly:1540890211880275988>",
    "Blockcustomwords":               "<:Blockcustomwords:1540890213239492608>",
    "blockmemberinteraction":         "<:blockmemberinteraction:1540890214757572678>",
    "Blockmessage":                   "<:Blockmessage:1540890215932231730>",
    "blockwordsinmemberprofilenames": "<:blockwordsinmemberprofilenames:1540890217194590288>",
    "boostlevel1":                    "<:boostlevel1:1540890226098962452>",
    # -- Category icons, used by the /config panel (one per log category;
    # `channels` has none of its own and keeps `channls`). ------------------
    "apps_icon":         "<:apps_icon:1540896689316036629>",
    "Webhooks_icon":     "<:Webhooks_icon:1540896690419142656>",
    "Poll_icon":         "<:Poll_icon:1540896691522248754>",
    "stage_icon":        "<:stage_icon:1540896692772151326>",
    "events_icon":       "<:events_icon:1540896693929644102>",
    "Soundboards_icon":  "<:Soundboards_icon:1540896695162773614>",
    "Stickers_icon":     "<:Stickers_icon:1540896696509399110>",
    "Emojis_icon":       "<:Emojis_icon:1540896697893388371>",
    "automod_icon":      "<:automod_icon:1540896699302674432>",
    "invitemember_icon": "<:invitemember_icon:1540896701131268106>",
    "voice_icon":        "<:voice_icon:1540896702360330370>",
    "threads_icon":      "<:threads_icon:1540896703719280681>",
    "roleicon_icon":     "<:roleicon_icon:1540896705250066502>",
    "mod_icon":          "<:mod_icon:1540896706546237520>",
    "members_icon":      "<:members_icon:1540896707854733412>",
    "messages_icon":     "<:messages_icon:1540896709863936160>",
    "Servers_icon":      "<:Servers_icon:1540896711378075668>",

}


def log_emoji(name: str, default: str = "") -> str:
    """Server-log icon by name — ``""`` when the name is unknown.

    Returning empty rather than raising is deliberate: a typo in an icon name
    must never stop a log from being delivered.
    """
    return LOG_EMOJIS.get(name, default)


# ---------------------------------------------------------------------------
# Dark icon variants (per-user "icon theme")
# ---------------------------------------------------------------------------
# Most icons above are white, which is invisible on Discord's light theme.
# A black twin named ``<original name>_dark`` is uploaded for them. Users who
# pick the light theme in /preferences get those twins swapped in centrally at
# send time (see utils/icon_theme.py); no call site changes. To add one: upload
# ``<name>_dark`` and add it to DARK_VARIANTS. See docs/guides/ICON_THEME.md.
DARK_VARIANTS: Dict[str, int] = {
    "1__dark": 1558775913238364180,
    "2__dark": 1558775915008364689,
    "3__dark": 1558775917545922601,
    "4__dark": 1558775919626162258,
    "add_dark": 1558776138330013696,
    "at_dark": 1558776109288521739,
    "attribution_dark": 1558776100950253638,
    "back_dark": 1558776178838478912,
    "balance_dark": 1558776249290326096,
    "banner_dark": 1558776146592538624,
    "blacklist_dark": 1558776199944339456,
    "book_dark": 1558776104880447510,
    "bug_dark": 1558776165358108712,
    "build_dark": 1558776740313038990,
    "claim_dark": 1558776738282999828,
    "code_dark": 1558776167681499227,
    "color_dark": 1558776236619071540,
    "commands_dark": 1558776170168852560,
    "commit_dark": 1558776260895969313,
    "cooldown_dark": 1558776226041045062,
    "copyright_dark": 1558776246937059380,
    "data_object_dark": 1558776177089450096,
    "delete_dark": 1558776181141278843,
    "dinvites_dark": 1558776749125402735,
    "dollars_dark": 1558776742271918110,
    "done_dark": 1558776230629740584,
    "download_dark": 1558776189680750614,
    "edit_dark": 1558776183318118480,
    "emoji_dark": 1558776238586466385,
    "error_dark": 1558776125671604245,
    "exclamation_dark": 1558776298980114432,
    "explore_dark": 1558776221456932864,
    "extension_dark": 1558776142540963850,
    "eye_m_dark": 1558776159498403920,
    "face_dark": 1558776152959488110,
    "favorite_dark": 1558776103236149318,
    "filter_dark": 1558776267879227523,
    "flag_dark": 1558776111691989013,
    "folder_dark": 1558776282836111470,
    "folders_dark": 1558776270475501628,
    "FrameBug_dark": 1558776263009632259,
    "groups_dark": 1558776116942999592,
    "hand_dark": 1558776294391418890,
    "history_dark": 1558776192709169182,
    "ia_dark": 1558776187575083059,
    "idea_dark": 1558776240851263548,
    "image_dark": 1558776284685795358,
    "import_dark": 1558776253530636319,
    "info_dark": 1558776163223212173,
    "invalidsnowflake_dark": 1558776207401680997,
    "label_dark": 1558776234677243964,
    "legal_dark": 1558776242575122475,
    "link_dark": 1558776292135149589,
    "lock_dark": 1558776223570726923,
    "logout_dark": 1558776172744146984,
    "manageuser_dark": 1558776215156822036,
    "message_dark": 1558776130306179142,
    "mic_off_dark": 1558776265413107733,
    "ModdyIcon_dark": 1558776087507374160,
    "ModdyIconCarre_dark": 1558776084777009172,
    "ModdyIconCarreMin_dark": 1558776090028281877,
    "network_ping_dark": 1558776148803063868,
    "next_dark": 1558776136362893382,
    "note_dark": 1558776132831154206,
    "notifications_dark": 1558776161067073600,
    "pause_dark": 1558776091689226272,
    "pending_dark": 1558776286774558750,
    "person_off_dark": 1558776185411084339,
    "play_dark": 1558776094247751732,
    "labs_dark": 1560741864154333225,
    "policy_dark": 1558776244789710960,
    "premium_dark": 1558776174962810971,
    "railway_dark": 1558776068150919170,
    "random_dark": 1558776150631915561,
    "reply_dark": 1558776123586908160,
    "rocket_launch_dark": 1558776759409844284,
    "rss_dark": 1558776096718200902,
    "save_dark": 1558776144503767171,
    "search_dark": 1558776127743336608,
    "send_dark": 1558776157531541675,
    "server_dark": 1558776098618085439,
    "settings_dark": 1558776228364816435,
    "shapes_dark": 1558776762329075722,
    "shield_dark": 1558776289232683019,
    "snowflake_dark": 1558776205040296036,
    "star_dark": 1558776107157950565,
    "support_dark": 1558776202775371856,
    "sync_dark": 1558776256516857886,
    "text_dark": 1558776140519444570,
    "threads_dark": 1558776728581574756,
    "ticket_dark": 1558776735296917614,
    "time_dark": 1558776213084839997,
    "toogle_off_dark": 1558776732197191713,
    "toogle_on_dark": 1558776730443841550,
    "track_dark": 1558776195145932831,
    "translate_dark": 1558776258643628093,
    "undone_dark": 1558776232508915715,
    "update_dark": 1558776251404132443,
    "user_dark": 1558776217229066412,
    "verified1_dark": 1558776219489804298,
    "voice_chat_dark": 1558776296354619473,
    "warning_dark": 1558776119446994975,
    "waving_hand_dark": 1558776114594324480,
    "web_dark": 1558776209909882880,
    "webhook_dark": 1558776155337916506,
    "fingerprint_dark": 1558791866286350437,
    "newspaper_dark": 1558791864373743718,
    "assignment_dark": 1558791858505646211,
    "timer_dark": 1558791856844701867,
    "refresh_dark": 1558791852382093332,
}

# Original ids that are hardcoded outside this module (other .py files or the
# locale files) or that predate the constants above. name -> extra ids.
_EXTRA_ORIGINAL_IDS: Dict[str, Tuple[int, ...]] = {
    "attribution": (1519788726476079104,),
    "data_object": (1519795407453159474,),
    "extension": (1519792289625538713,),
    "face": (1519793231699775578,),
    "favorite": (1519788838292295781,),
    "FrameBug": (1519805334988787843,),
    "label": (1519800544456343744,),
    "network_ping": (1519792879613247540,),
    "notifications": (1519793619861508147,),
    "railway": (1451333311199838218,),
    "random": (1519793103563788390,),
    "send": (1519793414185553940,),
    "server": (1519788576420921476,),
    "warning": (1398729560895422505,),
    # Listed in docs/guides/EMOJIS.md but not used as a constant yet — mapped so they
    # switch as soon as someone uses them.
    "1_": (1386154327499997275,),
    "2_": (1386154384974413906,),
    "3_": (1386154437998940300,),
    "4_": (1386154478142750874,),
    "color": (1519800727231266858,),
    "commit": (1519802920097419334,),
    "cooldown": (1519799889045880912,),
    "copyright": (1519801539143667785,),
    "explore": (1519799371841933483,),
    "eye_m": (1519793508695933130,),
    "ia": (1519796401796546581,),
    "idea": (1519801074427363468,),
    "import": (1519802254759039201,),
    "invalidsnowflake": (1519797857635532890,),
    "person_off": (1519796161530171603,),
    "policy": (1519801425247604736,),
    "track": (1519797032917795008,),
    "update": (1519801949736927414,),
    "verified1": (1519799054631043332,),  # white "moderated by Moddy" check
    "lock": (1519799617087213779,),
}

_EMOJI_TOKEN_RE = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")


def _iter_module_emoji_strings(value: Any, depth: int = 0):
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_module_emoji_strings(k, depth + 1)
            yield from _iter_module_emoji_strings(v, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            yield from _iter_module_emoji_strings(v, depth + 1)


def _build_light_to_dark() -> Dict[int, Tuple[str, int]]:
    """Map every original emoji id to ``(dark name, dark id)``, matching by name."""
    ids_by_name: Dict[str, set] = {}
    for key, value in list(globals().items()):
        if key.startswith("__") or key in ("DARK_VARIANTS", "LIGHT_TO_DARK"):
            continue
        for text in _iter_module_emoji_strings(value):
            for m in _EMOJI_TOKEN_RE.finditer(text):
                ids_by_name.setdefault(m.group(1), set()).add(int(m.group(2)))
    for name, ids in _EXTRA_ORIGINAL_IDS.items():
        ids_by_name.setdefault(name, set()).update(ids)

    mapping: Dict[int, Tuple[str, int]] = {}
    for dark_name, dark_id in DARK_VARIANTS.items():
        original = dark_name[: -len("_dark")]
        for original_id in ids_by_name.get(original, ()):
            mapping[original_id] = (dark_name, dark_id)
    return mapping


LIGHT_TO_DARK: Dict[int, Tuple[str, int]] = _build_light_to_dark()
