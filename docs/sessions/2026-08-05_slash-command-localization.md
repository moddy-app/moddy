# 2026-08-05 — Localisation des noms de commandes slash (32 langues)

## Objectif

Traduire le **nom** et la **description** de chaque commande slash (ainsi que
ses paramètres et les menus contextuels) dans la langue Discord de
l'utilisateur, via les champs `name_localizations` / `description_localizations`
de l'API Discord. Le contenu des réponses reste géré par le système i18n
existant.

## Ce qui a été fait

### 1. Traducteur de commandes (`utils/command_translator.py`)

`ModdyCommandTranslator(app_commands.Translator)` :

- charge `locales/commands/<locale>.json` (une fiche par locale Discord) ;
- résout les clés selon le contexte fourni par discord.py :
  `command_name`, `command_description`, `group_name`, `group_description`,
  `parameter_name`, `parameter_description`, `choice_name` ;
- les commandes sont indexées par **qualified name** (`"interserver report"`),
  donc aucun conflit entre un groupe et ses sous-commandes ;
- les paramètres sont résolus **commande d'abord, puis `common.parameters`** —
  `incognito` n'est traduit qu'une fois par langue ;
- valide chaque valeur avant de la renvoyer (regex de nom Discord, minuscules,
  ≤ 32 car., descriptions ≤ 100 car.) et logge un warning au lieu de lever :
  un nom invalide ferait échouer **tout** le sync ;
- toute clé absente renvoie `None` → repli sur l'anglais.

### 2. Fichiers de traduction (`locales/commands/*.json`)

Les **32 locales** de `discord.Locale`, avec les 30 commandes de l'arbre
(dont le groupe `interserver` et ses 2 sous-commandes), leurs paramètres et les
4 menus contextuels (`Save Message`, `Get Emojis`, `Translate`, `AI text tools`).

Exemples : `/avatar` → `/アバター` (ja), `/roll` → `/würfeln` (de),
`/mycases` → `/mes-dossiers` (fr), `/ban` → `/забанить` (ru).

### 3. Branchement (`bot.py`)

`await self.tree.set_translator(ModdyCommandTranslator())` dans `setup_hook()`,
avant `sync_commands()` — la traduction est *lazy* et n'est appliquée qu'au sync
(global dans `setup_hook()`, guild-only dans `on_ready()`).

### 4. Validation (`tests/test_command_localizations.py`)

193 tests hors-ligne : locales connues, validité des noms, longueur des
descriptions, unicité des noms (top-level et par groupe), unicité des noms de
paramètres dans une commande, parité des clés avec `en-US.json` (fichier de
référence). Les noms ont aussi été passés dans `validate_name()` /
`validate_context_menu_name()` de discord.py : 0 rejet.

### 5. Correctif annexe

`cogs/interserver_commands.py` : la description du GroupCog venait du docstring
français de la classe et remontait telle quelle aux utilisateurs anglais. Elle
est désormais déclarée explicitement en anglais
(`description="Manage inter-server messages"`).

## Fichiers modifiés / créés

- `utils/command_translator.py` *(nouveau)*
- `locales/commands/*.json` *(nouveau — 32 fichiers)*
- `tests/test_command_localizations.py` *(nouveau)*
- `docs/COMMAND_LOCALIZATION.md` *(nouveau)*
- `bot.py` — import + `set_translator()` dans `setup_hook()`
- `cogs/interserver_commands.py` — description du groupe en anglais
- `CLAUDE.md`, `docs/COMMANDS.md` — structure, règle i18n, index docs

## Décisions

- **Un fichier par locale** plutôt qu'un gros fichier multi-langues : le nom du
  fichier est le code de locale Discord, la résolution est directe et une langue
  peut être ajoutée sans toucher aux autres.
- **Clé = qualified name anglais**, pas la chaîne libre : deux commandes peuvent
  partager la même description anglaise sans se marcher dessus.
- **`common.parameters`** pour éviter de répéter `incognito`/`user`/`case` dans
  chaque commande × 32 langues, tout en autorisant une description spécifique
  par commande.
- **Commandes staff non traduites** (`/dev`, `/team`, `/mod`, `/manage`, …) :
  usage interne, anglais uniquement. Idem `/testerror`.
- **Choix de `/translate` non traduits** : ce sont des endonymes
  (`Français`, `Deutsch`…), identiques dans toutes les langues. La section
  `choices` existe dans le format si besoin plus tard.
- **Validation permissive à l'exécution** : une entrée invalide est ignorée et
  loggée, jamais levée — un fichier mal édité ne doit pas empêcher le bot de
  démarrer.

## Suivis possibles

- Les 4 commandes de modération (`ban`/`kick`/`mute`/`warn`) utilisent la
  description commune d'`incognito` (« Rendre la réponse visible uniquement pour
  vous ») au lieu de leur variante anglaise « Show the confirmation only to you
  (default: True) ». Sémantiquement équivalent ; à surcharger si besoin.
- Les traductions ont été rédigées par relecture directe, sans passe native pour
  chaque langue : un retour de la communauté sur `hi`, `th`, `lt`, `hr` serait
  utile.
- Toute nouvelle commande doit être ajoutée aux 32 fichiers (sinon elle reste en
  anglais, sans erreur).
