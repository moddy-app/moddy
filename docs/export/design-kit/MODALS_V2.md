# Modals V2 — guide complet (discord.py ≥ 2.6 / 2.7)

> Un modal est la fenêtre de saisie qui s'ouvre par-dessus Discord. Les
> Modals V2 permettent bien plus que des champs texte : selects, rôles, salons,
> cases à cocher, boutons radio, upload de fichiers et texte d'explication.
>
> - Labels / selects / TextInput-dans-Label / TextDisplay : **discord.py 2.6**
> - FileUpload, Checkbox, CheckboxGroup, RadioGroup : **discord.py 2.7**

```bash
pip install -U "discord.py>=2.7"
```

---

## 1. Principe

Un modal contient **au maximum 5 composants de premier niveau**, chacun étant :

- un `ui.Label` (un libellé + une description + **un** composant de saisie), ou
- un `ui.TextDisplay` (texte markdown sans saisie).

```python
class FeedbackModal(BaseModal, title="Ton avis"):     # title : 45 caractères max
    ...
```

Chez Moddy, **tous les modals héritent de `BaseModal`** (erreurs routées vers le
gestionnaire central, voir [DESIGN.md](DESIGN.md) §1).

---

## 2. Ouvrir un modal

```python
await interaction.response.send_modal(MyModal(...))
```

- ✔ depuis une commande slash, un bouton, un select.
- ✘ depuis une commande préfixe (pas d'interaction) → Moddy affiche un bouton
  « Ouvrir » qui, lui, ouvre le modal.
- ✘ depuis un autre modal (impossible d'enchaîner deux modals).
- ✘ après un `defer()` ou un `send_message()` : **ouvrir le modal doit être la
  première réponse** à l'interaction. Ne fais aucun travail lent avant
  (`send_modal` dans les 3 secondes).

---

## 3. `ui.Label`

```python
ui.Label(
    text="Nom",                          # 45 caractères max
    description="Comment t'appeler ?",   # 100 caractères max, optionnel
    component=ui.TextInput(...),
)
```

Composants autorisés dans un `Label` : `TextInput`, `Select`, `UserSelect`,
`RoleSelect`, `ChannelSelect`, `MentionableSelect`, `Checkbox`,
`CheckboxGroup`, `RadioGroup`, `FileUpload`.

---

## 4. `ui.TextDisplay` dans un modal

Texte statique (markdown). Pattern Moddy : **une ligne d'aide courte en haut du
modal**, qui dit ce qu'on attend ou rappelle les variables disponibles.

```python
self.add_item(ui.TextDisplay("-# Variables : `{user}`, `{server}`, `{count}`"))
```

Un modal avec seulement des `TextDisplay` sert aussi de confirmation lisible.

---

## 5. Les composants

### 5.1 `TextInput`

```python
ui.TextInput(
    style=discord.TextStyle.short,     # ou .paragraph (multi-lignes)
    placeholder="Ex. : Bienvenue !",   # 100 car. max
    default=current_value,             # pré-remplissage, 4000 car. max
    min_length=0, max_length=2000,     # 0–4000 / 1–4000
    required=True,
)
```

⚠️ Le paramètre `label` de `TextInput` est **déprécié** : c'est le `Label`
parent qui porte le texte.

### 5.2 `Select` (options)

```python
ui.Select(
    options=[discord.SelectOption(label="A", value="a", default=True),
             discord.SelectOption(label="B", value="b")],
    min_values=1, max_values=1,     # 25 options max
    required=True,                  # n'a d'effet que dans un modal
)
```

`min_values` doit être absent ou ≥ 1 si `required` est `True`.

### 5.3 `UserSelect` / `RoleSelect` / `ChannelSelect` / `MentionableSelect`

```python
ui.ChannelSelect(channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
```

Valeurs pré-sélectionnées : `default_values=[obj, ...]`.

| Composant | `.values` renvoie |
|---|---|
| `UserSelect` | `list[Member \| User]` |
| `RoleSelect` | `list[Role]` |
| `ChannelSelect` | `list[AppCommandChannel \| AppCommandThread]` (objets **partiels** : récupère le vrai salon avec `guild.get_channel(c.id)`) |
| `MentionableSelect` | `list[Role \| Member \| User]` |

### 5.4 `Checkbox` (2.7)

```python
ui.Checkbox(default=True)     # .value -> bool
```

Pas de `required` : pour une case **obligatoire**, utilise un `CheckboxGroup`
à une option (§5.6).

### 5.5 `CheckboxGroup` (2.7)

```python
ui.CheckboxGroup(
    options=[discord.CheckboxGroupOption(label="Messages", value="msg", default=True),
             discord.CheckboxGroupOption(label="Membres", value="members")],
    min_values=0, max_values=2,   # 10 options max
    required=False,
)
```

### 5.6 Case obligatoire (méthode officielle)

```python
ui.CheckboxGroup(
    options=[discord.CheckboxGroupOption(label="J'accepte les conditions", value="ok")],
    min_values=1, max_values=1, required=True,
)
```

### 5.7 `RadioGroup` (2.7) — choix unique

```python
ui.RadioGroup(
    options=[discord.RadioGroupOption(label="Toujours", value="auto", default=True),
             discord.RadioGroupOption(label="Jamais", value="never")],
    required=True,               # 2 à 10 options
)
```

⚠️ `RadioGroupOption` (comme `CheckboxGroupOption`) est sur `discord`, **pas**
sur `discord.ui`.

### 5.8 `FileUpload` (2.7)

```python
ui.FileUpload(min_values=1, max_values=1, required=True)   # .values -> list[discord.Attachment]
```

- Discord n'envoie que des `Attachment` (URL CDN) : télécharge-les toi-même
  (`await att.read()`), vérifie la taille et le type.
- Impossible de limiter la taille côté composant (c'est la limite d'upload de
  l'utilisateur).
- **Ne jamais exécuter** le contenu d'un fichier envoyé.

---

## 6. ⚠️ Lire les valeurs : `.value` ou `.values`

C'est **le** piège des Modals V2 : se tromper ne plante qu'**à la soumission**
(`AttributeError`), jamais à la construction ni à l'affichage.

| Composant | Accès |
|---|---|
| `TextInput`, `Checkbox`, **`RadioGroup`** | `.value` (un seul) |
| `Select`, `UserSelect`, `RoleSelect`, `ChannelSelect`, `MentionableSelect`, `CheckboxGroup`, `FileUpload` | `.values` (liste) |

Si tu déclares le `Label` en attribut de classe, la valeur est sur
`self.mon_label.component.value`. Si tu gardes une référence directe au
composant, `self.mon_input.value`.

---

## 7. Deux façons de déclarer

### 7.1 Attributs de classe (modal fixe)

```python
class FeedbackModal(BaseModal, title="Feedback"):
    feedback = ui.Label(
        text="Ton avis",
        component=ui.TextInput(style=discord.TextStyle.paragraph, max_length=1000),
    )

    async def on_submit(self, interaction: discord.Interaction):
        text = self.feedback.component.value
        ...
```

### 7.2 `add_item` dans `__init__` (textes traduits, valeurs pré-remplies) — **le cas Moddy**

```python
class WelcomeMessageModal(BaseModal):
    def __init__(self, bot, *, locale: str, current: str = ""):
        super().__init__(title=t("welcome.modal.title", locale=locale)[:45])
        self.bot = bot
        self.locale = locale

        self.add_item(ui.TextDisplay(f"-# {t('welcome.modal.help', locale=locale)}"))

        self.message = ui.TextInput(style=discord.TextStyle.paragraph,
                                    default=current or None, max_length=2000)
        self.add_item(ui.Label(
            text=t("welcome.modal.message", locale=locale)[:45],
            description=t("welcome.modal.message_hint", locale=locale)[:100],
            component=self.message,
        ))

        self.mode = ui.RadioGroup(options=[
            discord.RadioGroupOption(label=t("welcome.mode.channel", locale=locale), value="channel", default=True),
            discord.RadioGroupOption(label=t("welcome.mode.dm", locale=locale), value="dm"),
        ])
        self.add_item(ui.Label(text=t("welcome.modal.mode", locale=locale)[:45], component=self.mode))

    async def on_submit(self, interaction: discord.Interaction):
        text = self.message.value.strip()
        mode = self.mode.value                     # RadioGroup -> .value
        if not text:
            raise InvalidInput("message")          # erreur attendue -> carte calme
        await save_config(interaction.guild_id, text=text, mode=mode)
        # Le modal a été ouvert depuis un bouton du panneau : on met le panneau à jour.
        await interaction.response.edit_message(view=await WelcomeConfigView.load(interaction))
```

Coupe toujours les textes traduits (`[:45]`, `[:100]`) : une traduction trop
longue fait échouer l'ouverture du modal.

> Les décorateurs `@ui.label` / `@ui.text_input` **n'existent pas**. Seuls
> `@ui.button` et `@ui.select` existent (pour les vues).

---

## 8. Répondre à la soumission

| Situation | Réponse |
|---|---|
| Modal ouvert depuis un bouton/select d'un panneau | `interaction.response.edit_message(view=...)` → le panneau se met à jour sur place |
| Modal ouvert depuis une commande slash | `interaction.response.send_message(view=..., ephemeral=True)` |
| Traitement lent | `await interaction.response.defer(ephemeral=True)` (ou carte de chargement) puis `followup` / `edit_original_response` |
| Saisie invalide | lever une erreur attendue (ou répondre une carte d'erreur éphémère) ; l'utilisateur rouvre le modal |

Pattern Moddy : **le modal affiche la valeur actuelle** (`default=`), et le
panneau qui l'ouvre affiche cette valeur en texte (c'est le seul cas où une
ligne « valeur actuelle » est utile — un bouton qui ouvre un modal ne montre
rien, voir [DESIGN.md](DESIGN.md) §5.2).

---

## 9. Modals et persistance

Les modals **ne sont pas persistants** et n'ont pas à l'être : un modal ouvert
au moment d'un redémarrage est perdu, l'utilisateur le rouvre.
`ui.Modal` a déjà `timeout=None`, il n'expire pas tant que le bot tourne.

Ce qui doit être persistant, c'est le **bouton qui ouvre le modal** : son
callback reconstruit le modal à partir de l'interaction (locale, valeur
actuelle relue en base) — voir [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## 10. Secrets

Tout secret (mot de passe, token, JWT…) se demande **dans un modal**, jamais
dans une option slash : les options de commandes finissent dans les logs. Ce
qui est tapé dans le modal n'est ni loggé, ni stocké, ni mis en cache, et la
réponse est éphémère.

---

## 11. Limites

| Élément | Limite |
|---|---|
| Composants de premier niveau | **5** (`Label` ou `TextDisplay`) |
| `Modal.title` | 45 |
| `Modal.custom_id` / `custom_id` d'un composant | 100 |
| `Label.text` | 45 |
| `Label.description` | 100 |
| `TextInput.placeholder` | 100 |
| `TextInput.min_length` / `max_length` | 0–4000 / 1–4000 |
| `TextInput.default` | 4000 |
| Options `Select` | 25 |
| `Select.placeholder` | 150 |
| Options `CheckboxGroup` | 10 |
| Options `RadioGroup` | 2–10 |
| Fichiers `FileUpload` | 10 |
| Libellé / valeur / description d'option (Checkbox/Radio) | 100 |

---

## 12. Pièges connus

1. **`disabled=True`** sur un composant de modal → erreur API.
2. **`row=`** sur un `TextInput` : ignoré/peu fiable ; l'ordre d'affichage est
   l'ordre d'ajout.
3. **`TextInput(label=...)`** : déprécié, utilise `Label(text=...)`.
4. **`.values` sur un `RadioGroup`** → `AttributeError` à la soumission.
5. **`send_modal` après `defer`** → impossible.
6. **`ChannelSelect`** renvoie des salons partiels : passe par
   `guild.get_channel(id)` avant d'utiliser des méthodes de salon.

---

## 13. Migration depuis l'ancienne API

```python
# Ancien (déprécié)
class Old(ui.Modal, title="Feedback"):
    feedback = ui.TextInput(label="Ton avis", style=discord.TextStyle.paragraph)

# V2
class New(BaseModal, title="Feedback"):
    feedback = ui.Label(text="Ton avis",
                        component=ui.TextInput(style=discord.TextStyle.paragraph))
```

Discord : *« All interactive components in modals should be placed inside
Label components instead. »*
