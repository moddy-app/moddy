# Le design Moddy — logique, règles, recettes

> Ce document explique **comment Moddy pense ses interfaces** et donne toutes les
> règles pour reproduire exactement le même rendu sur un autre bot. Il est
> autonome : tu n'as pas besoin du code de Moddy pour le suivre (les bouts de
> code à copier sont dedans).
>
> Docs techniques associées : [COMPONENTS_V2.md](COMPONENTS_V2.md),
> [MODALS_V2.md](MODALS_V2.md), [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## 0. La philosophie en 6 phrases

1. **Simple d'abord.** Une interface doit se comprendre sans explication. S'il
   faut lire un tuto pour cliquer, c'est trop compliqué.
2. **Une seule apparence.** Toutes les réponses (commande, panneau de config,
   erreur, DM, log) ont la même anatomie : un titre avec icône, le contenu, les
   détails, une ligne grise de pied, les boutons. L'utilisateur apprend à lire
   *une* carte, puis il sait toutes les lire.
3. **La couleur a un sens, jamais une décoration.** Bleu = info, vert = succès
   (et rien d'autre), jaune = attention / simulation, rouge = erreur, gris = en
   pause / neutre.
4. **Pas de bruit.** On n'affiche pas ce que l'interface montre déjà (une valeur
   déjà visible dans un select, un compteur que le bouton désactivé dit déjà,
   un séparateur entre chaque ligne).
5. **Rien ne meurt.** Chaque bouton marche encore après un redémarrage ou une
   semaine d'inactivité (vues persistantes, voir
   [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md)).
6. **On dit la vérité.** On ne confirme jamais une action que Discord a refusée,
   on dit d'où vient une information, on dit qui a écrit un message.

---

## 1. Les briques obligatoires

| Règle | Pourquoi |
|---|---|
| **Components V2 uniquement** (`ui.LayoutView` + `ui.Container` + `ui.TextDisplay`). **Jamais** `discord.Embed()`. | Rendu plus propre, texte + boutons dans le même bloc, une seule façon de faire. |
| **Toutes les vues héritent de `BaseView`**, tous les modals de `BaseModal`. | Les erreurs sont attrapées et envoyées au gestionnaire central ; `timeout=None` par défaut. |
| **Émojis custom uniquement** (pas d'émoji Unicode, sauf les drapeaux de pays). | Identité visuelle cohérente. Le script `copy_emojis_jsk.py` te copie tous ceux de Moddy. |
| **Tout texte affiché passe par l'i18n** (`t("cle", locale=locale)`). | Le bot parle la langue de l'utilisateur ; aucune chaîne en dur. |
| **Toutes les vues interactives sont persistantes.** | Un bouton « This interaction failed » est un bug. |

### `BaseView` / `BaseModal` minimal à copier

```python
# ui/base.py
import logging
from typing import Optional
import discord
from discord import ui

log = logging.getLogger("bot.ui")


async def report_interaction_error(interaction: discord.Interaction, error: Exception, where: str):
    """Point unique : log + carte d'erreur à l'utilisateur. Branche ici ton
    gestionnaire d'erreurs (code d'erreur, Sentry, salon d'erreurs...)."""
    log.error("UI error in %s", where, exc_info=error)
    from ui.cards import error_card  # voir §6
    card = error_card("Une erreur est survenue", "Réessaie dans un instant.")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(view=card, ephemeral=True)
        else:
            await interaction.response.send_message(view=card, ephemeral=True)
    except discord.HTTPException:
        pass


class BaseView(ui.LayoutView):
    """Toutes les vues du bot. timeout=None : rien n'expire en mémoire."""
    __persistent__: bool = False

    def __init__(self, *, timeout: Optional[float] = None, **kwargs):
        super().__init__(timeout=timeout, **kwargs)
        self.bot = None

    @classmethod
    def register_persistent(cls, bot) -> None:
        raise NotImplementedError(f"{cls.__name__} doit implémenter register_persistent")

    async def on_error(self, interaction, error, item):
        await report_interaction_error(interaction, error, f"View:{type(self).__name__}")


class BaseModal(ui.Modal):
    """Tous les modals du bot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = None

    async def on_error(self, interaction, error):
        await report_interaction_error(interaction, error, f"Modal:{type(self).__name__}")
```

> Dans une vue/un modal, **n'attrape pas les exceptions inattendues** : laisse-les
> remonter, `on_error` s'en occupe. N'attrape que ce que tu sais traiter.

---

## 2. L'anatomie d'une carte

Toutes les réponses suivent ce squelette :

```
┌ Container (accent = sens) ─────────────────────────────┐
│ -# Config › Tickets › Panneau Support   ← fil d'Ariane (sous-écrans seulement)
│ ### <icône> Titre                        ← toujours ### + icône devant
│ Phrase d'explication courte.             ← description
│ ──────────                               ← séparateur (seulement si utile)
│ **Champ**<required>                      ← libellé en gras (+ icône si obligatoire)
│ -# Ce que fait ce champ                  ← aide en gris
│ [ Select / boutons du champ ]
│ **Label** · `valeur`                     ← détails : valeurs dynamiques en backticks
│ ──────────
│ -# Fourni par <logo> **Service** · demandé par @user   ← pied, une ligne grise
└────────────────────────────────────────────────────────┘
[ Retour ] [ Enregistrer ] [ Annuler ]   ← barre d'actions, HORS du container
```

### 2.1 Titres

Toujours `### <:emoji:id> Titre`. Jamais `#`/`##`, jamais un titre en gras,
jamais l'icône après le texte, jamais un titre sans icône.

```python
ui.TextDisplay(f"### {emojis.SETTINGS} {t('config.title', locale=locale)}")
```

- **Une seule icône par titre.**
- Les sous-parties d'une longue carte utilisent un **sous-titre en gras**
  (`**Section**`), pas un autre `###`.

### 2.2 Texte gris `-#`

`-#` en début de ligne = petit texte gris. On l'utilise pour : les aides sous
un champ, le pied de carte, le fil d'Ariane, les précisions secondaires.

```python
ui.TextDisplay(f"**{t('field.channel')}**\n-# {t('field.channel.help')}")
```

### 2.3 Valeurs dynamiques entre backticks

Tout ce qui change d'un utilisateur à l'autre (ID, nombre, nom saisi, durée,
valeur de config) est entre backticks :

```python
f"**Utilisateur :** {user.mention} (`{user.id}`)"
f"**Serveur :** `{guild.name}` (`{guild.id}`)"
f"Tu pourras réessayer dans `{seconds}s`."
```

Neutralise les backticks d'une valeur saisie par l'utilisateur avant de
l'entourer (`value.replace("`", "ˋ")`), sinon il casse la mise en forme.

Exceptions : les mentions (`<@id>`, `<#id>`, `<@&id>`) et les noms dans un fil
d'Ariane (c'est de la navigation, pas une donnée).

### 2.4 Séparateurs : rarement

`ui.Separator(spacing=discord.SeparatorSpacing.small)` seulement :
- sous l'en-tête (titre + description) avant les champs,
- avant un avertissement important,
- avant le pied de carte.

Jamais entre chaque champ, jamais entre le titre et la description, jamais
« pour faire joli ». `large` : quasiment jamais.

### 2.5 Listes d'éléments : des citations

Un écran qui liste plusieurs éléments du même type (rappels, messages,
panneaux…) affiche **chaque élément comme une citation** (`> ` sur chaque
ligne). Les barres de citation séparent déjà les éléments :

```python
def quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.split("\n"))

for entry in entries:
    container.add_item(ui.TextDisplay(quote(f"**{entry.name}**\n-# {entry.detail}")))
```

- **Pas de séparateur entre les éléments**, ni autour de la liste.
- **Pas de lignes de remplissage** : pas de titre de liste qui répète le titre
  de l'écran, pas de « 3 élément(s) », pas de ligne de quota que le bouton
  « Ajouter » désactivé dit déjà. Un `3/5` est OK si le maximum est fixe et
  nulle part ailleurs.

### 2.6 Fil d'Ariane (sous-écrans)

Chaque sous-écran (pas l'écran principal) commence par un fil d'Ariane gris,
**premier élément du container, au-dessus du titre** :

```python
def breadcrumb(*parts: str) -> ui.TextDisplay:
    return ui.TextDisplay("-# " + " › ".join(p for p in parts if p))

container.add_item(breadcrumb("Config", "Automod", "Sanctions", "Doxxing"))
container.add_item(ui.TextDisplay(f"### {emojis.SHIELD} Doxxing"))
```

### 2.7 Pied de carte : dire d'où vient l'info

Quand une réponse s'appuie sur un service externe, on le **nomme avec une
phrase verbale**, jamais `Source : X` :

```
-# Traduit par <logo> **DeepL**
-# Fourni par <logo> **Wiktionary** · demandé par @user
-# Trouvé par <logo> **SauceNAO** et <logo> **trace.moe**
```

- Le logo est **collé au nom** auquel il appartient, pas en début de ligne.
- Calculé par le bot lui-même → pas de ligne de source du tout.
- « demandé par @user » seulement sur les réponses publiques.

---

## 3. Les couleurs (accent du container)

```python
COLORS = {
    "primary":   0x5865F2,  # bleu Discord — info, état actif
    "success":   0x57F287,  # vert — UNIQUEMENT "c'est fait / enregistré / ok"
    "warning":   0xFEE75C,  # jaune — attention, simulation, avertissement
    "error":     0xED4245,  # rouge — erreur
    "critical":  0x992D22,  # rouge foncé — bug inattendu / sanction grave
    "neutral":   0x99AAB5,  # gris — en pause, désactivé
    "developer": 0x9B59B6,  # violet — outils staff/dev
}
ui.Container(accent_colour=discord.Colour(COLORS["primary"]))
```

| Situation | Accent |
|---|---|
| Information, résultat neutre | `primary` |
| Action réussie, config enregistrée, verdict positif | `success` |
| Avertissement, mode simulation, verdict douteux | `warning` |
| Erreur attendue (mauvaise saisie, permission…) | `warning` ou `error` selon gravité |
| Bug | `critical` |
| Module en pause | `neutral` |

**Le vert n'est jamais décoratif.** Une carte d'info n'est pas verte. Une
erreur n'est jamais verte.

**L'accent suit l'état** d'un module dans son panneau : actif = `primary`,
en pause = `neutral`, simulation = `warning`.

---

## 4. Les icônes

### 4.1 Règles

- **Que des émojis custom**, centralisés dans **un seul fichier**
  (`utils/emojis.py`) sous forme de constantes : `DONE = "<:done:123…>"`. On ne
  colle jamais un ID en dur ailleurs : on importe la constante. Changer une
  icône = changer une ligne.
- Seule exception : les **drapeaux** de pays (🇫🇷 🇬🇧) pour les langues.
- **Pas d'icône « à peu près ».** Si aucune icône ne colle vraiment à ce que tu
  montres, n'en prends pas une vaguement proche et ne réutilise pas une icône
  qui veut déjà dire autre chose : mets **pas d'icône** pour l'instant et crée
  la bonne.
- Une icône = un sens. `delete` veut dire supprimer partout, `back` veut dire
  retour partout.

### 4.2 Vocabulaire de base

| Sens | Constante | Usage |
|---|---|---|
| Succès | `DONE` | titres de confirmation |
| Annuler / non | `UNDONE` | bouton Annuler, état « non » |
| Erreur | `ERROR` | cartes d'erreur |
| Attention | `WARNING` | avertissements |
| Info | `INFO` | cartes d'information |
| Paramètres | `SETTINGS` | titres de config |
| Ajouter / Modifier / Supprimer | `ADD` / `EDIT` / `DELETE` | boutons d'action |
| Enregistrer | `SAVE` | bouton Enregistrer |
| Retour / Suivant | `BACK` / `NEXT` | navigation |
| Chargement | `LOADING` (animé) | cartes « en cours » |
| Champ obligatoire | `REQUIRED_FIELDS` | après le libellé d'un champ requis |
| Utilisateur | `USER` | cartes de profil |

Le fichier `emojis.py` généré par le script jsk contient **toutes** les
constantes de Moddy avec les IDs de ton bot.

### 4.3 Thème clair (les jumeaux `_dark`)

Les icônes de Moddy sont **blanches** : invisibles sur le thème clair de
Discord. Pour beaucoup, il existe une jumelle noire nommée `<nom>_dark` (le
script les copie aussi, liste dans `DARK_VARIANTS`).

Discord ne dit pas aux bots quel thème l'utilisateur utilise. Moddy fait donc :
une préférence `/preferences → thème`, et **au moment de l'envoi** un patch
remplace chaque `<:nom:id>` par `<:nom_dark:id>` si l'utilisateur a choisi le
thème clair. Règles :
- **Ne jamais choisir la variante `_dark` à la main dans le code.**
- Les messages publics que le bot envoie de lui-même (logs, bienvenue,
  panneaux) restent en blanc.

C'est optionnel pour un nouveau bot : commence sans, ajoute-le plus tard.

---

## 5. Les panneaux de configuration

### 5.1 Structure d'un écran

```python
container = ui.Container(accent_colour=state_accent(state))
container.add_item(breadcrumb("Config", "Bienvenue"))                 # sous-écran
container.add_item(ui.TextDisplay(f"### {emojis.WAVE} {t('welcome.title')}"))
container.add_item(ui.TextDisplay(t("welcome.description")))
container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

# Champ obligatoire
container.add_item(ui.TextDisplay(
    f"**{t('welcome.channel')}**{emojis.REQUIRED_FIELDS}\n-# {t('welcome.channel.help')}"
))
row = ui.ActionRow(); row.add_item(channel_select); container.add_item(row)

# Champ optionnel (pas d'icône)
container.add_item(ui.TextDisplay(f"**{t('welcome.role')}**\n-# {t('welcome.role.help')}"))
row = ui.ActionRow(); row.add_item(role_select); container.add_item(row)

self.add_item(container)
self.add_item(action_row)  # barre d'actions HORS du container
```

- Champ = **libellé en gras** (+ `REQUIRED_FIELDS` collé s'il est obligatoire)
  + retour à la ligne + **aide `-#`** + le contrôle juste en dessous.
- Les selects montrent la valeur actuelle (`default_values` /
  `SelectOption(default=True)`).

### 5.2 « Valeur actuelle » : seulement si rien d'autre ne la montre

- **Ne pas** écrire `Valeur actuelle : …` sous un `ChannelSelect`,
  `RoleSelect`, `UserSelect`, `MentionableSelect` ou un `Select` avec une option
  `default=True` : le contrôle affiche déjà sa valeur, la répéter = doublon qui
  peut diverger.
- **Écrire** la valeur actuelle quand le réglage passe par quelque chose qui
  n'affiche rien : un bouton qui ouvre un modal (message perso, couleur,
  seuil…), un bouton toggle, une valeur stockée sans composant.

Règle simple : si on peut lire la réponse sur le contrôle, la ligne de texte
est du bruit ; si le contrôle est un bouton qui ouvre un modal, la ligne est le
seul moyen de savoir ce qui est enregistré.

### 5.3 Les boutons d'action

| Bouton | Icône | Style | Quand |
|---|---|---|---|
| Retour | `BACK` | `secondary` | toujours sur un sous-écran ; **désactivé** si des modifs sont en attente |
| Enregistrer | `SAVE` | `success` | seulement s'il y a des modifs en attente |
| Annuler | `UNDONE` | `danger` | seulement s'il y a des modifs en attente |
| Gérer | — | `secondary` | ouvre l'écran « état du module / supprimer » |

Deux modes possibles par écran, à choisir et à garder cohérent :
- **Sauvegarde instantanée** : chaque clic enregistre tout de suite (le plus
  simple, et obligatoire sur les écrans faits de `DynamicItem`).
- **Brouillon + Enregistrer/Annuler** : on édite une copie de travail, on
  enregistre en une fois. Après un redémarrage, le brouillon non enregistré est
  perdu — c'est accepté.

### 5.4 État d'un module et bouton « Gérer »

L'état (actif / en pause / simulation) **n'est pas affiché dans l'écran
principal**. Un petit bouton secondaire **Gérer**, dans la barre du bas, ouvre
un écran avec :
- la ligne d'état (`**État** <icône> Actif` + une explication `-#`),
- un select dont chaque option explique un état en une ligne (plus clair que
  des boutons Pause / Reprendre),
- le bouton rouge « Supprimer la configuration » avec confirmation **sur place**
  (le premier clic réaffiche l'écran en mode confirmation, le « Oui » est
  lui-même un bouton persistant).

### 5.5 Feedback immédiat

- Chaque interaction **met à jour l'écran tout de suite**
  (`interaction.response.edit_message(view=nouvelle_vue)`).
- Les boutons indisponibles sont **désactivés**, pas cachés quand leur présence
  aide à comprendre.
- Les actions destructrices demandent une confirmation.
- Le feedback personnel (erreur, « ce n'est pas ton panneau ») est **éphémère**.

---

## 6. Les cartes de résultat, de chargement et d'erreur

### 6.1 Helpers à copier

```python
# ui/cards.py
import discord
from discord import ui
from utils import emojis

COLORS = {"primary": 0x5865F2, "success": 0x57F287, "warning": 0xFEE75C,
          "error": 0xED4245, "critical": 0x992D22, "neutral": 0x99AAB5}


def card(title: str, body: str = "", *, icon: str = "", accent: str = "primary",
         details=(), footer: str = "", view: ui.LayoutView | None = None) -> ui.LayoutView:
    view = view or ui.LayoutView()
    c = ui.Container(accent_colour=discord.Colour(COLORS[accent]))
    c.add_item(ui.TextDisplay(f"### {icon} {title}".replace("###  ", "### ")))
    if body:
        c.add_item(ui.TextDisplay(body))
    if details:
        c.add_item(ui.TextDisplay("\n".join(f"**{k}** · `{v}`" for k, v in details)))
    if footer:
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        c.add_item(ui.TextDisplay(f"-# {footer}"))
    view.add_item(c)
    return view


def success_card(title, body="", **kw):
    return card(title, body, icon=emojis.DONE, accent="success", **kw)


def info_card(title, body="", **kw):
    return card(title, body, icon=emojis.INFO, accent="primary", **kw)


def error_card(title, body="", *, hint: str = "", severity: str = "error"):
    icon = {"info": emojis.INFO, "warning": emojis.WARNING, "error": emojis.ERROR}.get(severity, emojis.ERROR)
    accent = {"info": "primary", "warning": "warning"}.get(severity, "error")
    return card(title, body + (f"\n-# {hint}" if hint else ""), icon=icon, accent=accent)


def loading_card(text: str):
    return card(text, icon=emojis.LOADING, accent="neutral")
```

### 6.2 Chargement

Tout ce qui peut prendre plus d'~1 s répond **d'abord** avec une carte de
chargement, puis **édite** ce message avec le résultat :

```python
await interaction.response.send_message(view=loading_card(t("ocr.loading")), ephemeral=True)
result = await do_the_work()
await interaction.edit_original_response(view=info_card(...))
```

(Rappel : Discord exige une réponse en moins de **3 secondes**.)

### 6.3 Résultat

- **Le résultat principal en premier**, dans un bloc de code si c'est quelque
  chose à copier (hash, mot de passe, UUID, JSON).
- Puis les détails `**Label** · \`valeur\``.
- Puis le pied gris.

### 6.4 Deux familles d'erreurs

| | Erreur **attendue** | **Bug** |
|---|---|---|
| Exemples | permission manquante, saisie invalide, introuvable, cooldown, module désactivé | exception non prévue |
| Carte | calme, uniforme, accent jaune/rouge/bleu selon gravité | rouge foncé, icône bug, **code d'erreur**, boutons Support |
| Log | INFO max, pas de Sentry | traceback complet, Sentry, salon d'erreurs |

Le mieux : une famille d'exceptions (`BotError` → `NotFound`, `InvalidInput`,
`MissingPermissions`, `Cooldown`…) que tu **lèves** dans le code, et que
`report_interaction_error` transforme en carte calme. Comme ça l'utilisateur
distingue « ma saisie » de « leur bug ».

### 6.5 Ton des messages d'erreur

- **Calme, direct**, tutoiement/vouvoiement cohérent. Pas d'excuses, pas de
  reproche, pas de point d'exclamation, pas de jargon, **jamais le texte brut
  d'une exception**.
- **Ce qui s'est passé + quoi faire.** Description : 1-2 phrases courtes (10-20
  mots). Titre : 2-5 mots, un groupe nominal.

| ✔ Faire | ✘ Ne pas faire |
|---|---|
| `Permission manquante` / Il te faut `Gérer les rôles`. Demande à un admin si besoin. | `Erreur !! Tu n'as pas la permission d'exécuter cette commande.` |
| `Introuvable` / Je n'ai pas trouvé `123`. Vérifie et réessaie. | `Oups, quelque chose s'est mal passé :(` |
| `Doucement` / Tu pourras réessayer dans `4s`. | `Cooldown actif, attendre 4.0 secondes` |

---

## 7. Les noms d'utilisateur

- Utilise le **nom d'affichage** (`global_name`) plutôt que le username.
- Hors mention, un nom s'écrit en gras : `**{name}**`.
- (Moddy ajoute un badge de vérification collé au nom, `**{name}**{badge}`,
  pour les comptes vérifiés — à reproduire si ton bot a un système similaire.)

---

## 8. Langue (i18n)

- **Aucune chaîne en dur** : `t("cle.chemin", locale=locale, **variables)`.
- Fichiers JSON par langue (`locales/fr.json`, `locales/en-US.json`…).
- **Qui lit le message décide de la langue :**
  - ce qu'**une personne** lit en privé (réponse éphémère, panneau de config,
    erreur) → la langue **de l'utilisateur** (`interaction.locale`, ou une
    langue qu'il a forcée dans ses préférences) ;
  - ce que **tout un serveur** lit (message de bienvenue, panneau public, log)
    → **une seule** langue du serveur, réglée une fois pour tout le bot.
- **Ajouter un menu « langue » dans la config d'un module est un bug** : il y a
  un seul réglage de langue par serveur.
- Les **noms et descriptions de commandes slash** sont localisés à part
  (`app_commands.locale_str` + un `Translator`), Discord les affiche dans la
  langue de chacun.
- Les variables dans les traductions : `{name}`, `{count}`… et les backticks
  font partie de la traduction (`"Je n'ai pas trouvé `{what}`."`).

---

## 9. Visibilité des réponses

- Feedback personnel, erreurs, panneaux de config : **éphémère**.
- Option « incognito » (préférence utilisateur) pour les commandes utilitaires :
  éphémère par défaut, public si l'utilisateur le veut.
- Toujours éphémère pour ce qui touche à un secret (mots de passe, tokens).
- Une réponse publique qui contient du texte d'utilisateur part avec
  `allowed_mentions=discord.AllowedMentions.none()`.
- **Un secret ne passe jamais par une option slash** (elles finissent dans les
  logs) : on le demande dans un modal.

---

## 10. Robustesse visible

- On **ne confirme jamais** une action que Discord a refusée : on attend le
  résultat de l'appel, on attrape `Forbidden`/`NotFound`, on affiche la vraie
  issue.
- Les permissions manquantes du bot sont annoncées **toutes en même temps**,
  en nommant chaque permission, avec un lien de réinvitation adapté.
- On n'affiche jamais `channel.name` d'un salon que le bot ne voit pas : on
  utilise `<#id>`.
- Le bot ne demande jamais `Administrator` : chaque fonctionnalité déclare les
  permissions précises dont elle a besoin.

---

## 11. Checklist avant de livrer une interface

- [ ] Components V2 (`ui.Container`, `ui.TextDisplay`), aucun `discord.Embed`
- [ ] Vue qui hérite de `BaseView`, modal qui hérite de `BaseModal`
- [ ] Vue **persistante** (custom_id namespacés, enregistrée au démarrage)
- [ ] Titre `### <icône> Titre`, une seule icône
- [ ] Icônes custom uniquement, importées depuis `emojis.py`, pas d'icône « à peu près »
- [ ] Valeurs dynamiques entre backticks
- [ ] Aides et pied en `-#`
- [ ] Séparateurs rares
- [ ] Listes en citations, sans lignes de remplissage
- [ ] Fil d'Ariane en haut des sous-écrans
- [ ] Champs requis marqués `REQUIRED_FIELDS`
- [ ] Pas de « valeur actuelle » sous un contrôle qui l'affiche déjà
- [ ] Accent = sens (vert seulement pour un succès)
- [ ] Carte de chargement puis édition pour ce qui est lent
- [ ] Erreurs attendues : ton calme, quoi faire ensuite, pas de texte d'exception
- [ ] Tout le texte via i18n ; bonne langue (utilisateur vs serveur)
- [ ] Réponses personnelles éphémères ; aucun secret en option slash
