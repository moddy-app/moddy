# Vues persistantes — guide complet

> Chez Moddy, **chaque bouton et chaque menu doit marcher pour toujours** :
> après 3 minutes d'inactivité, après un redémarrage, après un déploiement.
> Une vue interactive non persistante est un **bug**, au même titre qu'un texte
> codé en dur ou un embed.

---

## 1. Pourquoi

Sans persistance :
- discord.py fait expirer une vue après `timeout` secondes (**180 s** par
  défaut) ;
- **toutes** les vues meurent au redémarrage du bot. L'utilisateur voit
  « Cette interaction a échoué » à chaque clic.

Avec persistance :
- `BaseView` met `timeout=None` par défaut → rien n'expire en mémoire ;
- les vues enregistrées au démarrage survivent au redémarrage : discord.py
  route les clics vers une instance « coquille » qui reconstruit son état à
  partir de l'interaction.

---

## 2. Comment discord.py route un clic

Une vue est **persistante** si :
1. `timeout=None`, **et**
2. chaque composant interactif a un `custom_id` fixe (les boutons-liens et les
   composants non interactifs comptent comme persistants).

On l'enregistre **une fois** au démarrage :
- `bot.add_view(MaVue())` pour une vue classique ;
- `bot.add_dynamic_items(MonItem)` pour un `DynamicItem`.

Au clic, discord.py cherche `(type de composant, custom_id)` :
- **Vue classique** : si la vue envoyée avec ce message est encore en mémoire,
  c'est elle qui reçoit le clic ; sinon (redémarrage…), c'est **la coquille
  enregistrée** — une seule instance partagée par tous les messages, sans état.
- **`DynamicItem`** : discord.py enregistre la **classe** et une regex
  (`template=`). À **chaque** clic (redémarrage ou pas), il reconstruit une
  instance neuve via `from_custom_id()`. Le `DynamicItem` n'a donc **que** ce
  qui est encodé dans son `custom_id` + ce qu'il relit en base.

> **Règle d'or : un callback ne s'appuie jamais sur `self.locale`,
> `self.user_id`, `self.config`… Il re-dérive tout depuis `interaction`.**

---

## 3. Convention des `custom_id`

Format : `<bot>:<cog>:<vue>:<action>[:<param>...]` — par ex.
`mybot:config:welcome:save`, `mybot:rem:manage:delete:123456789`.

Déclare-les en **constantes de module** (`_CID_SAVE = "mybot:config:welcome:save"`),
pour qu'un `grep` les retrouve tous et qu'une faute de frappe ne casse rien en
silence.

| Portée | Exemple | Commentaire |
|---|---|---|
| Sans état | `mybot:main:about` | Le callback déduit tout de l'interaction |
| Utilisateur | `mybot:rem:manage:add:<user_id>` | Seul le propriétaire peut cliquer |
| Serveur | `mybot:config:welcome:channel` | `interaction.guild_id` suffit + permission revérifiée |
| Entité | `mybot:cases:view:<case_id>` | L'ID désigne une ligne en base |
| Page | `mybot:saved:page:<user_id>:<page>` | La page est un petit entier |

**Les collisions sont un vrai bug silencieux** : deux vues enregistrées avec le
même `custom_id` (`"back_btn"`…) → l'une masque l'autre. D'où le préfixe
complet, et le test du §9.

**Les templates de `DynamicItem` ne doivent pas se chevaucher** : discord.py
garde les items dans un dict indexé par la regex compilée et prend le premier
qui matche. Deux classes avec la **même** regex → une seule entrée, la
dernière gagne, les autres boutons meurent. Une regex distincte par classe.

**Ne jamais mettre dans un `custom_id`** : la langue (relis-la depuis
l'interaction), un secret / token / URL de webhook, quoi que ce soit que
l'utilisateur ne doit pas voir (le client Discord voit les `custom_id`).

---

## 4. Le contrat d'une vue classique persistante

```python
import discord
from discord import ui
from ui.base import BaseView

_CID_REFRESH = "mybot:stats:main:refresh"


class StatsView(BaseView):
    """Carte de stats. Persistante : oui. Auth : publique."""

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US"):
        super().__init__()                     # timeout=None
        self.bot = bot
        self.locale = locale
        self.build_view()

    def build_view(self):
        self.clear_items()
        c = ui.Container()
        c.add_item(ui.TextDisplay(f"### {emojis.ANALYTICS} {t('stats.title', locale=self.locale)}"))
        # Tout ce qui lit l'état du bot est protégé : la coquille a bot=None.
        if self.bot is not None:
            c.add_item(ui.TextDisplay(f"**Serveurs** · `{len(self.bot.guilds)}`"))
        self.add_item(c)

        # Les composants interactifs sont TOUJOURS présents, avec un custom_id fixe.
        row = ui.ActionRow()
        btn = ui.Button(label=t("stats.refresh", locale=self.locale),
                        emoji=discord.PartialEmoji.from_str(emojis.SYNC),
                        style=discord.ButtonStyle.secondary, custom_id=_CID_REFRESH)
        btn.callback = self.on_refresh
        row.add_item(btn)
        self.add_item(row)

    async def on_refresh(self, interaction: discord.Interaction):
        # Tout vient de l'interaction : self peut être la coquille vide.
        locale = get_user_locale(interaction)
        await interaction.response.edit_message(view=StatsView(interaction.client, locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_view(cls())                    # coquille : bot=None, valeurs par défaut
```

Les 6 points :
1. `__persistent__ = True` ;
2. un `custom_id` fixe et namespacé sur chaque composant interactif ;
3. `__init__` accepte d'être appelé **sans argument** (`bot=None`, défauts partout) ;
4. tout accès à `self.bot.xxx` dans la construction est protégé par
   `if self.bot is not None:` ;
5. `register_persistent(cls, bot)` ;
6. la classe est ajoutée au **registre** (§8).

---

## 5. `DynamicItem` : quand l'état doit voyager avec le bouton

Dès qu'un bouton dépend d'un **propriétaire**, d'une **entité** ou d'une
**page** qu'on ne peut pas déduire de l'interaction, on encode cette donnée
dans le `custom_id` avec un `DynamicItem`.

```python
import re
import discord
from discord import ui


def guarded(callback):
    """Un DynamicItem n'a pas de BaseView.on_error : sans ce wrapper, une
    exception disparaît sans que l'utilisateur ni les logs ne la voient."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            await report_interaction_error(interaction, e, type(self).__name__)
    return wrapper


class ReminderButton(ui.DynamicItem[ui.Button],
                     template=r"mybot:rem:manage:(?P<action>add|delete):(?P<owner>\d+)"):
    """Bouton du gestionnaire de rappels. Auth : propriétaire uniquement."""

    def __init__(self, action: str, owner_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t(f"reminder.buttons.{action}", locale=locale)[:80],
            emoji=discord.PartialEmoji.from_str(emojis.ADD if action == "add" else emojis.DELETE),
            style=discord.ButtonStyle.success if action == "add" else discord.ButtonStyle.danger,
            custom_id=f"mybot:rem:manage:{action}:{owner_id}",
        ))
        self.action = action
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["action"], int(match["owner"]))

    @guarded
    async def callback(self, interaction: discord.Interaction):
        locale = get_user_locale(interaction)
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                view=error_card(t("errors.not_yours.title", locale=locale),
                                t("errors.not_yours.description", locale=locale), severity="warning"),
                ephemeral=True)
            return
        reminders = await db.reminders.list(self.owner_id)        # données fraîches
        ...
        await interaction.response.edit_message(view=build_manager(self.owner_id, reminders, locale))


class ReminderPersistence:
    """Marqueur pour le registre : n'enregistre que les items."""
    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ReminderButton)
```

Utilisation dans une vue : `row.add_item(ReminderButton("add", user.id, locale=locale))`.

Points clés :
- `from_custom_id` reçoit le `re.Match` → reconstruit l'item.
- La vue qui contient les items n'a **pas besoin** d'être enregistrée (elle ne
  peut pas être construite sans l'entité, et les items se suffisent).
- **Enveloppe chaque callback avec `guarded`.**
- Petits entiers et IDs seulement : le `custom_id` est limité à **100
  caractères**.

---

## 6. Les deux pièges qu'on s'est tous pris

### 6.1 Ne jamais renvoyer `view=self` sur une vue enregistrée

```python
# ✘ DANGEREUX
self.working_config["channel"] = x
self.build_view()
await interaction.response.edit_message(view=self)
```

`bot.add_view(cls())` enregistre **une seule** instance partagée. Si deux
serveurs tombent sur la coquille au même moment et que le callback modifie
`self`, la modif d'un serveur apparaît sur le panneau de l'autre.

```python
# ✔
async def on_channel(self, interaction):
    if self.bot is None or self.guild_id != interaction.guild_id:
        config = await load_config(interaction.guild_id)      # coquille ou instance périmée
    else:
        config = dict(self.working_config)                    # vraie instance du message
    config["channel"] = ...
    view = WelcomeConfigView(interaction.client, interaction.guild_id,
                             get_user_locale(interaction), working_config=config)
    await interaction.response.edit_message(view=view)
```

Les `DynamicItem` n'ont pas ce problème (instance neuve à chaque clic).

### 6.2 Les boutons conditionnels doivent exister dans la coquille

Si « Enregistrer » / « Annuler » n'apparaissent que quand `has_changes` est
vrai, la coquille construite avec les valeurs par défaut ne les déclare pas :
après un redémarrage, ces boutons ne mènent nulle part.

```python
def build_view(self):
    is_shell = self.bot is None
    ...
    if self.has_changes or is_shell:
        row.add_item(self._save_button())
        row.add_item(self._cancel_button())
    if not self.has_changes or is_shell:
        row.add_item(self._delete_button())
```

Sur la coquille (jamais envoyée à personne), on rend **toutes** les variantes
pour que tous les `custom_id` soient connus.

---

## 7. Autorisation : qui a le droit de cliquer

Choisis le modèle le plus simple et écris-le en commentaire au-dessus de
`register_persistent`.

| Type de vue | Modèle | Comment |
|---|---|---|
| Informatif public | **Public** | Aucun contrôle |
| Données personnelles (rappels, préférences, favoris) | **Propriétaire** | `user_id` dans le `custom_id` (`DynamicItem`), comparé à `interaction.user.id` |
| Panneau de config d'un serveur | **Permission serveur** | `custom_id` fixe ; au clic, revérifier `interaction.user.guild_permissions.manage_guild` |
| Outils staff | **Rang staff** | Revérifier le rang à chaque clic |

Refus → **carte d'erreur éphémère** (jamais un silence). Une seule paire de
clés i18n partagée pour « ce n'est pas ton message ».

**Revérifie toujours au clic** : quelqu'un qui était admin quand la carte a été
postée ne l'est peut-être plus. Et si une donnée sensible voyage dans le
`custom_id` (un ensemble de permissions…), **refiltre-la** côté serveur : un
`custom_id` peut être forgé.

---

## 8. Le registre et l'enregistrement au démarrage

```python
# ui/persistent_views.py
import logging
log = logging.getLogger("bot.persistent")


def _collect_persistent_view_classes():
    from cogs.stats import StatsView
    from cogs.reminder import ReminderPersistence
    from modules.configs.welcome import WelcomeConfigView
    return [StatsView, ReminderPersistence, WelcomeConfigView]


def register_all_persistent_views(bot) -> None:
    for cls in _collect_persistent_view_classes():
        try:
            cls.register_persistent(bot)
        except Exception:
            # Une vue cassée ne doit jamais empêcher le bot de démarrer.
            log.exception("Persistent view %s failed to register", cls.__name__)
```

```python
# bot.py
async def setup_hook(self):
    await self.load_extensions()
    register_all_persistent_views(self)
```

---

## 9. Le test qui protège tout

`bot.add_view(v)` accepte **sans rien dire** une vue non persistante ; les
clics n'arrivent juste jamais. C'est le test qui attrape ça.

```python
# tests/test_persistent_views.py
import re
import pytest
from discord import ui
from ui.persistent_views import _collect_persistent_view_classes

CID_RE = re.compile(r"^mybot:[a-z0-9_]+:[a-z0-9_]+:[a-z0-9_]+(:[\w-]+)*$")
VIEWS = [c for c in _collect_persistent_view_classes() if issubclass(c, ui.LayoutView)]


def _custom_ids(view):
    for item in view.walk_children():
        cid = getattr(item, "custom_id", None)
        if cid and getattr(item, "url", None) is None:
            yield cid


@pytest.mark.parametrize("cls", VIEWS, ids=lambda c: c.__name__)
def test_view_is_persistent(cls):
    view = cls()                                  # la coquille se construit sans argument
    assert cls.__persistent__ is True
    assert view.timeout is None
    assert view.is_persistent()
    for cid in _custom_ids(view):
        assert CID_RE.match(cid), cid


def test_no_duplicate_custom_ids():
    seen = {}
    for cls in VIEWS:
        for cid in set(_custom_ids(cls())):
            assert cid not in seen, f"{cid} dans {cls.__name__} et {seen[cid]}"
            seen[cid] = cls.__name__
```

Ajoute aussi un test qui vérifie que les `template` de tes `DynamicItem` ne se
chevauchent pas (un `custom_id` d'exemple par classe ne doit matcher que sa
propre regex).

---

## 10. Recette : rendre une vue persistante

1. Choisis le modèle d'autorisation (§7), écris-le en une ligne.
2. Rends tous les arguments du constructeur optionnels (`MaVue()` doit
   marcher). Pas de référence à une vue parente : « Retour » reconstruit le
   parent depuis la base.
3. Protège les `self.bot.xxx` de la construction par `if self.bot is not None:`.
4. Un `custom_id` constant et namespacé sur chaque bouton/select.
5. Réécris les callbacks pour tout tirer de `interaction` ; jamais `view=self`.
6. Données propriétaire/entité/page → `DynamicItem` + `template=`.
7. Boutons conditionnels → échappatoire `is_shell`.
8. `__persistent__ = True` + `register_persistent`.
9. Ajoute la classe au registre.
10. Lance le test.

---

## 11. État qui ne tient pas dans un `custom_id`

Par ordre de préférence :
1. l'état **dans le `custom_id`** (IDs, page, action) ;
2. l'état **relu depuis le message** (`interaction.message`) ou la base ;
3. un **jeton** : l'état est stocké (Redis, TTL 24 h…) et seul un jeton court
   va dans le `custom_id`. Quand le jeton a expiré, le clic répond « relance la
   commande ». Accepté pour les saisies que l'utilisateur peut retaper.

Les brouillons non enregistrés d'un panneau de config sont perdus au
redémarrage : le panneau se reconstruit depuis la config enregistrée. Accepté.

---

## 12. Exceptions acceptées (justifiées)

Une vue non persistante doit avoir une **raison concrète** — « c'était plus
simple » n'en est pas une. Celles que Moddy accepte :

- **Les modals** : soumission à usage unique, perdue au redémarrage, on rouvre.
- **Les cartes d'erreur** : boutons-liens uniquement (ou aucun bouton), rien à
  enregistrer ; la carte d'erreur de secours ne doit pas dépendre du registre
  qu'elle signale peut-être en panne.
- **Les vues qui affichent un secret** (token, URL de webhook) : on ne veut pas
  qu'elles soient ré-affichables après un redémarrage.
- **Les confirmations dont l'action est une fonction Python en mémoire**
  (« Confirmer cette requête SQL ? ») : pas d'identité stable, et exécuter
  après un redémarrage une action saisie dans une vie précédente du processus
  serait dangereux. Timeout court volontaire.
- **Les vues sans composant interactif** ou avec seulement des boutons-liens :
  rien à enregistrer, ne pas mettre `__persistent__`.
