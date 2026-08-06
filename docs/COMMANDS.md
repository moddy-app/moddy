# Guide de Création de Commandes Moddy

Ce document explique comment créer et gérer les commandes slash (app commands) dans Moddy, notamment la différence entre les commandes globales et les commandes spécifiques aux serveurs.

## Table des matières

1. [Types de Commandes](#types-de-commandes)
2. [Commandes Globales](#commandes-globales)
3. [Commandes Guild-Only](#commandes-guild-only)
4. [Comment ça fonctionne ?](#comment-ça-fonctionne)
5. [Exemples Pratiques](#exemples-pratiques)
6. [Bonnes Pratiques](#bonnes-pratiques)

---

## Types de Commandes

Moddy supporte deux types de commandes slash :

### 1. **Commandes Globales** (App-Perso)
- Accessibles **partout** : DMs, tous les serveurs Discord (même sans Moddy)
- Synchronisées globalement par Discord
- Exemples : `/ping`, `/user`, `/webhook`, `/avatar`, `/translate`

### 2. **Commandes Guild-Only** (Spécifiques aux serveurs)
- Accessibles **uniquement dans les serveurs où Moddy est présent**
- **Non accessibles** en DM
- **Non accessibles** dans les serveurs sans Moddy
- Synchronisées serveur par serveur
- Exemples : `/config`

---

## Commandes Globales

### Quand utiliser une commande globale ?

Utilisez une commande globale quand :
- ✅ La commande peut fonctionner en DM
- ✅ La commande ne nécessite pas de configuration serveur
- ✅ La commande est une fonctionnalité personnelle (profil utilisateur, traduction, etc.)
- ✅ Vous voulez que la commande soit accessible partout

### Comment créer une commande globale ?

**N'ajoutez PAS `@app_commands.guild_only()` et ajoutez les décorateurs de contexte**

```python
import discord
from discord import app_commands
from discord.ext import commands

class MonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Check the bot's latency"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ping(self, interaction: discord.Interaction):
        """Cette commande sera GLOBALE - accessible partout"""
        await interaction.response.send_message(f"Pong! {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    await bot.add_cog(MonCog(bot))
```

**Important** : Les décorateurs `allowed_installs` et `allowed_contexts` sont **obligatoires** pour que la commande soit disponible en DMs (depuis la mise à jour Discord 2024).

**Résultat** : `/ping` est disponible partout (DMs + tous les serveurs)

---

## Commandes Guild-Only

### Quand utiliser une commande guild-only ?

Utilisez une commande guild-only quand :
- ✅ La commande nécessite un serveur (ex: configuration, modération)
- ✅ La commande utilise des données spécifiques au serveur
- ✅ La commande ne doit être accessible que là où Moddy est installé
- ✅ La commande ne peut pas fonctionner en DM

### Comment créer une commande guild-only ?

**Ajoutez le décorateur `@app_commands.guild_only()`**

```python
import discord
from discord import app_commands
from discord.ext import commands

class MonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="config",
        description="Configure server modules"
    )
    @app_commands.guild_only()  # ← Voici la ligne importante !
    async def config(self, interaction: discord.Interaction):
        """Cette commande sera GUILD-ONLY - uniquement dans les serveurs avec Moddy"""

        # Vous pouvez utiliser interaction.guild en toute sécurité
        # car guild_only garantit que interaction.guild n'est jamais None
        guild_name = interaction.guild.name
        await interaction.response.send_message(f"Configuration pour {guild_name}")

async def setup(bot):
    await bot.add_cog(MonCog(bot))
```

**Résultat** : `/config` est uniquement disponible dans les serveurs où Moddy est présent

---

## Comment ça fonctionne ?

### Système de Synchronisation Automatique

Le bot détecte automatiquement le type de commande grâce au décorateur `@app_commands.guild_only()` et synchronise les commandes de manière appropriée.

#### Phase 1 : Au démarrage - setup_hook() (`bot.py:182-204`)

```python
async def sync_commands(self):
    """
    1. Identifie les commandes guild-only et les sauvegarde dans self._guild_only_commands
    2. Les retire de l'arbre global (ET NE JAMAIS LES REMETTRE)
    3. Synchronise les commandes globales uniquement (sans guild-only)

    Note: À ce moment, self.guilds est VIDE car le bot n'est pas encore connecté.
    Les guild-only seront synchronisées dans on_ready().
    """
```

**Important** : Les guild-only sont **stockées dans `self._guild_only_commands`** et ne sont **jamais** remises dans l'arbre global.

#### Phase 2 : Après connexion - on_ready() (`bot.py:209-243`)

```python
async def sync_all_guild_commands(self):
    """
    Appelée dans on_ready() quand self.guilds est disponible.

    Pour chaque serveur où Moddy est présent:
       - Ajoute les guild-only UNIQUEMENT à l'arbre du serveur avec add_command(cmd, guild=guild)
       - Sync SEULEMENT les guild-only pour ce serveur avec sync(guild=guild)

    IMPORTANT: Ne PAS copier les commandes globales avec copy_global_to()
    car cela ferait que Discord ignore les commandes globales pour ce serveur.
    Les commandes globales sont déjà synchronisées globalement et disponibles partout.
    """
```

**Fonctionnement détaillé** :
- `tree.add_command(cmd, guild=guild)` ajoute la commande **uniquement** à l'arbre du serveur, pas à l'arbre global
- `tree.sync(guild=guild)` synchronise **uniquement** l'arbre de ce serveur spécifique
- Les arbres global et serveur sont **complètement séparés** dans discord.py
- Les guild-only ne sont **jamais** dans l'arbre global, uniquement dans les arbres des serveurs avec Moddy
- Les guild-only sont stockées dans `self._guild_only_commands` (cache permanent)
- **CRUCIAL**: Ne PAS utiliser `copy_global_to()` car si un serveur a des commandes synchronisées spécifiquement, Discord ignore les commandes globales pour ce serveur

#### Quand Moddy rejoint un nouveau serveur (`bot.py:563-577`)

```python
async def on_guild_join(self, guild: discord.Guild):
    # ...
    # Ajoute UNIQUEMENT les guild-only pour ce serveur
    await self.sync_guild_commands(guild)
```

**Résultat** : `/config` devient immédiatement disponible dans ce nouveau serveur (mais pas ailleurs), et toutes les commandes globales restent accessibles

#### Quand Moddy quitte un serveur (`bot.py:542-549`)

```python
async def on_guild_remove(self, guild: discord.Guild):
    # ...
    # Nettoie toutes les commandes de l'arbre du serveur
    self.tree.clear_commands(guild=guild)
    await self.tree.sync(guild=guild)
```

**Résultat** : `/config` n'est plus accessible dans ce serveur

### Détection du décorateur

Discord.py ajoute automatiquement un attribut `guild_only` à la commande :

```python
# Dans bot.py
for command in self.tree.walk_commands():
    if hasattr(command, 'guild_only') and command.guild_only:
        # Cette commande est guild-only
        guild_only_commands.append(command)
```

**Vous n'avez rien à faire** - le système détecte automatiquement le décorateur !

---

## Exemples Pratiques

### Exemple 1 : Commande de profil utilisateur (GLOBALE)

```python
from discord import app_commands, ui
from discord.ext import commands

class UserProfile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View a user's profile")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None):
        """Commande GLOBALE - accessible partout"""
        target = user or interaction.user

        embed = discord.Embed(
            title=f"Profile de {target.name}",
            description=f"ID: {target.id}"
        )

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(UserProfile(bot))
```

**Pourquoi GLOBALE ?**
- ✅ Peut fonctionner en DM
- ✅ Ne nécessite pas de configuration serveur
- ✅ Affiche des infos utilisateur universelles

---

### Exemple 2 : Commande de modération (GUILD-ONLY)

```python
from discord import app_commands
from discord.ext import commands

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.guild_only()  # ← Guild-only car c'est de la modération
    @app_commands.default_permissions(moderate_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str
    ):
        """Commande GUILD-ONLY - uniquement dans les serveurs"""

        # interaction.guild est garanti d'exister grâce à @guild_only()
        # interaction.guild n'est jamais None ici

        await interaction.response.send_message(
            f"⚠️ {member.mention} a été averti pour : {reason}"
        )

async def setup(bot):
    await bot.add_cog(Moderation(bot))
```

**Pourquoi GUILD-ONLY ?**
- ✅ Nécessite un serveur (on ne peut pas modérer en DM)
- ✅ Utilise `discord.Member` (spécifique aux serveurs)
- ✅ Ne doit être accessible que là où Moddy gère la modération

---

### Exemple 3 : Commande de configuration de module (GUILD-ONLY)

```python
from discord import app_commands, ui
from discord.ext import commands

class WelcomeConfig(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="setup-welcome",
        description="Configure the welcome module"
    )
    @app_commands.guild_only()  # ← Guild-only car configuration serveur
    @app_commands.default_permissions(manage_guild=True)
    async def setup_welcome(self, interaction: discord.Interaction):
        """Commande GUILD-ONLY - configuration serveur"""

        # Récupère la config du serveur
        config = await self.bot.module_manager.get_module_config(
            interaction.guild.id,
            'welcome'
        )

        view = WelcomeConfigView(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            "Configure le module de bienvenue :",
            view=view,
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(WelcomeConfig(bot))
```

**Pourquoi GUILD-ONLY ?**
- ✅ Configure un module spécifique au serveur
- ✅ Utilise des données serveur (`guild.id`)
- ✅ Ne peut pas fonctionner en DM

---

## Localisation du nom et de la description

Le `name` et la `description` déclarés dans le cog sont **toujours en anglais** :
ce sont les identifiants techniques. Discord affiche ensuite à chaque
utilisateur la variante correspondant à sa langue, grâce au `Translator`
branché sur l'arbre de commandes.

```python
# Déclaration : anglais, comme d'habitude
@app_commands.command(name="avatar", description="Display a user's avatar")
@app_commands.describe(user="The user whose avatar you want to see")
async def avatar_command(self, interaction, user: discord.User):
    ...
```

```json
// locales/commands/fr.json
"avatar": {
  "name": "avatar",
  "description": "Affiche l'avatar d'un utilisateur",
  "parameters": {
    "user": { "name": "utilisateur", "description": "L'utilisateur dont vous voulez voir l'avatar" }
  }
}
```

Après avoir créé une commande, ajoutez sa clé dans les fichiers
`locales/commands/*.json` (32 locales Discord) puis lancez
`pytest tests/test_command_localizations.py`. Sans entrée, la commande reste
affichée en anglais — rien ne casse.

Détails complets → [COMMAND_LOCALIZATION.md](COMMAND_LOCALIZATION.md)

---

## Bonnes Pratiques

### ✅ DO (À FAIRE)

1. **Utilisez `@guild_only()` pour les commandes serveur**
   ```python
   @app_commands.command(name="setup")
   @app_commands.guild_only()  # ← Bon !
   async def setup(self, interaction):
       pass
   ```

2. **Vérifiez `interaction.guild` pour les commandes globales si nécessaire**
   ```python
   @app_commands.command(name="serverinfo")
   async def serverinfo(self, interaction):
       if not interaction.guild:
           await interaction.response.send_message(
               "Cette commande doit être utilisée dans un serveur",
               ephemeral=True
           )
           return
       # ...
   ```

3. **Utilisez `@default_permissions()` avec `@guild_only()` pour la modération**
   ```python
   @app_commands.command(name="ban")
   @app_commands.guild_only()
   @app_commands.default_permissions(ban_members=True)
   async def ban(self, interaction, member: discord.Member):
       pass
   ```

### ❌ DON'T (À ÉVITER)

1. **N'utilisez PAS `@guild_only()` sur des commandes DM-friendly**
   ```python
   # ❌ Mauvais - empêche l'utilisation en DM
   @app_commands.command(name="ping")
   @app_commands.guild_only()  # ← Pas nécessaire pour ping !
   async def ping(self, interaction):
       pass
   ```

2. **N'oubliez PAS `@guild_only()` sur les commandes de configuration**
   ```python
   # ❌ Mauvais - config sera accessible partout
   @app_commands.command(name="config")
   # @app_commands.guild_only()  ← Manquant !
   async def config(self, interaction):
       # interaction.guild peut être None en DM !
       pass
   ```

3. **N'utilisez PAS `discord.Member` dans les commandes globales**
   ```python
   # ❌ Mauvais - Member ne fonctionne pas en DM
   @app_commands.command(name="profile")
   async def profile(self, interaction, member: discord.Member):
       pass

   # ✅ Bon - User fonctionne partout
   @app_commands.command(name="profile")
   async def profile(self, interaction, user: discord.User):
       pass
   ```

---

## Récapitulatif Rapide

| Type | Décorateur | Accessible en DM ? | Accessible sans Moddy ? | Exemples |
|------|------------|-------------------|------------------------|----------|
| **Globale** | *(aucun)* | ✅ Oui | ✅ Oui | `/ping`, `/user`, `/avatar` |
| **Guild-Only** | `@guild_only()` | ❌ Non | ❌ Non | `/config`, `/setup-welcome` |

---

## Questions Fréquentes

### Q: Comment savoir si ma commande doit être globale ou guild-only ?

**R:** Posez-vous ces questions :
- La commande peut-elle fonctionner en DM ? → Globale
- La commande nécessite-t-elle un serveur ? → Guild-only
- La commande configure-t-elle quelque chose sur le serveur ? → Guild-only
- La commande est-elle personnelle à l'utilisateur ? → Globale

### Q: Que se passe-t-il si j'oublie `@guild_only()` ?

**R:** La commande sera synchronisée globalement et accessible partout, même dans les serveurs sans Moddy. Cela peut causer des erreurs si la commande utilise `interaction.guild` sans vérifier qu'il n'est pas `None`.

### Q: Puis-je changer une commande de globale à guild-only ?

**R:** Oui ! Ajoutez simplement `@guild_only()` et redémarrez le bot. La prochaine synchronisation la rendra guild-only automatiquement.

### Q: Les changements sont-ils immédiats ?

**R:**
- **Au démarrage** : La synchronisation prend ~5-10 secondes
- **Guild join/remove** : Instantané (synchronisation automatique)
- **Modification de code** : Nécessite un redémarrage du bot

### Q: Comment le système garantit-il que les guild-only ne sont pas visibles partout ?

**R:** Le système utilise la séparation stricte entre les arbres de commandes de discord.py et un processus en 2 phases :

1. **Arbres séparés** : discord.py maintient deux dictionnaires complètement indépendants :
   - `_global_commands` : Arbre global
   - `_guild_commands[guild_id]` : Un arbre distinct par serveur

2. **Phase 1 - setup_hook() (avant connexion)** :
   - Les guild-only sont **retirées** de l'arbre global
   - Elles sont **stockées** dans `self._guild_only_commands` (cache permanent)
   - Sync globale **sans** les guild-only
   - **Important** : Les guild-only ne sont **JAMAIS** remises dans l'arbre global

3. **Phase 2 - on_ready() (après connexion)** :
   - À ce moment, `self.guilds` est disponible (liste des serveurs)
   - Pour chaque serveur : `add_command(cmd, guild=guild)` ajoute les guild-only **uniquement à ce serveur**
   - `sync(guild=X)` synchronise UNIQUEMENT les guild-only pour ce serveur
   - **IMPORTANT** : Ne PAS utiliser `copy_global_to()` car cela ferait que Discord ignore les commandes globales

4. **Pourquoi 2 phases ?** :
   - Dans `setup_hook()`, le bot n'est pas connecté → `self.guilds` est **vide**
   - Dans `on_ready()`, le bot est connecté → `self.guilds` contient tous les serveurs

Cette architecture garantit que les commandes guild-only ne peuvent **physiquement pas** être synchronisées globalement, car elles n'existent jamais dans l'arbre global au moment de la sync.

### Q: Pourquoi ne pas tout faire dans setup_hook() ?

**R:** Parce que `self.guilds` est **vide** dans `setup_hook()` ! Le bot n'a pas encore reçu la liste des serveurs de Discord. La boucle `for guild in self.guilds:` ne s'exécuterait jamais.

C'est pourquoi la synchronisation est divisée en 2 phases :
- **setup_hook()** : Sync les commandes globales (pas besoin de connaître les serveurs)
- **on_ready()** : Sync les guild-only pour chaque serveur (nécessite `self.guilds`)

### Q: Pourquoi ne pas utiliser `copy_global_to()` pour les serveurs ?

**R:** C'est un comportement subtil mais crucial de l'API Discord :

**Quand un serveur a des commandes synchronisées spécifiquement** (via `sync(guild=X)`), **Discord affiche UNIQUEMENT ces commandes et ignore complètement les commandes globales** pour ce serveur.

Donc si on fait :
```python
tree.copy_global_to(guild=X)  # Copie les globales dans l'arbre du serveur
tree.sync(guild=X)             # Sync l'arbre du serveur
```

Discord va :
1. Voir que le serveur X a des commandes synchronisées spécifiquement
2. Utiliser UNIQUEMENT ces commandes pour le serveur X
3. **Ignorer les commandes globales** pour le serveur X (y compris dans les DMs !)

**La solution** : Ne synchroniser QUE les guild-only pour chaque serveur. Les commandes globales restent synchronisées globalement et sont automatiquement disponibles partout (serveurs + DMs) sans avoir besoin de les copier.

### Q: Pourquoi mes commandes globales ne sont pas disponibles en DMs même après sync ?

**R:** Depuis la mise à jour Discord 2024, les commandes ont besoin de **contextes d'intégration explicites** pour être disponibles en DMs.

**Par défaut**, même les commandes globales sont uniquement disponibles dans les serveurs. Pour qu'elles soient disponibles en DMs, vous devez ajouter :

```python
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
```

**Explication** :
- `allowed_installs` : Où la commande peut être installée (serveurs ET profils utilisateurs)
- `allowed_contexts` : Où la commande peut être utilisée (serveurs, DMs, canaux privés)

**Sans ces décorateurs**, la commande sera globale mais uniquement disponible dans les serveurs, PAS en DMs.

**Avec ces décorateurs**, la commande sera vraiment disponible partout (serveurs + DMs).

---

## Contributeurs

Ce système a été développé pour résoudre le problème où les commandes guild-only (comme `/config`) étaient accessibles dans tous les serveurs, même ceux sans Moddy.

**Documentation créée le** : 29 novembre 2025
**Dernière mise à jour** : 30 novembre 2025

### Historique des corrections

- **30 novembre 2025** (final fix) : Ajout de `allowed_installs` et `allowed_contexts` à toutes les commandes globales. C'était le vrai problème : depuis Discord 2024, les commandes ont besoin de contextes d'intégration explicites pour être disponibles en DMs.
- **30 novembre 2025** (tentative) : Retrait de `copy_global_to()` et ajout de `clear_commands()` pour nettoyer les anciennes commandes. Cela n'a pas résolu le problème car la vraie cause était les contextes manquants.
- **29 novembre 2025** : Version initiale avec synchronisation en 2 phases (setup_hook + on_ready)
