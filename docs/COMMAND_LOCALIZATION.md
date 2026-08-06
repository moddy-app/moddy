# Localisation des commandes slash (nom + description)

> Ce document décrit comment le **nom** et la **description** des commandes
> slash de Moddy sont traduits dans la langue Discord de l'utilisateur.
> Pour le **contenu** des réponses (textes affichés après exécution), voir le
> système i18n classique (`utils/i18n.py`, `locales/fr.json`, `locales/en-US.json`).

---

## Principe

Discord permet de fournir un nom et une description **par langue** pour chaque
commande, groupe, paramètre et choix, via les champs `name_localizations` /
`description_localizations` de l'API. Le client Discord affiche automatiquement
la variante correspondant à la langue de l'utilisateur.

Concrètement :

| Langue du client | Commande affichée |
|---|---|
| English | `/avatar` — *Display a user's avatar* |
| Français | `/avatar` — *Affiche l'avatar d'un utilisateur* |
| 日本語 | `/アバター` — *ユーザーのアバターを表示します* |
| Deutsch | `/würfeln` — *Wirft einen zufälligen Würfel* |

Côté code **rien ne change** : les callbacks, `interaction.namespace`, les
checks et les logs continuent d'utiliser les noms anglais déclarés dans les
cogs. Seul l'affichage change.

---

## Architecture

```
utils/command_translator.py     ModdyCommandTranslator (app_commands.Translator)
locales/commands/<locale>.json  Une fiche de traduction par locale Discord (32)
tests/test_command_localizations.py   Validation hors-ligne des fichiers
bot.py::setup_hook()            await self.tree.set_translator(...)
```

discord.py appelle `Translator.translate(string, locale, context)` **au moment
du `tree.sync()`**, une fois par (chaîne, langue, contexte), puis envoie le
résultat à Discord. Retourner `None` signifie « pas de traduction » → Discord
conserve la chaîne anglaise d'origine.

⚠️ **La traduction n'est appliquée qu'au sync.** Modifier un fichier
`locales/commands/*.json` sans re-synchroniser l'arbre ne change rien côté
Discord. Un redémarrage du bot suffit (le sync global est fait dans
`setup_hook()`, les guild-only dans `on_ready()`).

---

## Langues couvertes

Les 32 locales supportées par Discord (`discord.Locale`) :

`bg`, `cs`, `da`, `de`, `el`, `en-GB`, `en-US`, `es-419`, `es-ES`, `fi`, `fr`,
`hi`, `hr`, `hu`, `id`, `it`, `ja`, `ko`, `lt`, `nl`, `no`, `pl`, `pt-BR`,
`ro`, `ru`, `sv-SE`, `th`, `tr`, `uk`, `vi`, `zh-CN`, `zh-TW`

Le nom du fichier **doit** être exactement le code de locale Discord
(`pt-BR.json`, pas `pt.json`) — un fichier inconnu est ignoré avec un warning.

---

## Format d'un fichier

`locales/commands/fr.json` :

```json
{
  "common": {
    "parameters": {
      "incognito": {
        "name": "incognito",
        "description": "Rendre la réponse visible uniquement pour vous"
      }
    }
  },
  "commands": {
    "avatar": {
      "name": "avatar",
      "description": "Affiche l'avatar d'un utilisateur",
      "parameters": {
        "user": {
          "name": "utilisateur",
          "description": "L'utilisateur dont vous voulez voir l'avatar"
        }
      }
    },
    "interserver report": {
      "name": "signaler",
      "description": "Signale un message inter-serveur à l'équipe de modération"
    }
  },
  "context_menus": {
    "Translate": { "name": "Traduire" }
  },
  "choices": {}
}
```

Règles de résolution :

- **Clé d'une commande** = son *qualified name* anglais. Une sous-commande
  s'écrit avec un espace : `"interserver report"`. Pas de collision possible
  entre un groupe et une sous-commande.
- **Paramètres** : Moddy cherche d'abord dans `commands.<cmd>.parameters.<param>`,
  puis dans `common.parameters.<param>`. C'est ce qui permet de traduire
  `incognito` une seule fois par langue, tout en gardant une description
  spécifique là où c'est utile (`avatar.user` ≠ `ban.user`).
- **Menus contextuels** : section `context_menus`, clé = nom anglais exact
  (`"Save Message"`, `"Get Emojis"`, `"Translate"`, `"AI text tools"`).
- **Choix** (`app_commands.Choice`) : section `choices`, clé = *valeur* du choix.
  Volontairement vide aujourd'hui : les choix de `/translate` sont des noms de
  langue dans leur propre langue (`Français`, `Deutsch`…), identiques partout.
- **Toute clé absente = repli sur l'anglais.** Un fichier partiel est donc
  toujours sûr.

---

## Contraintes Discord (validées automatiquement)

Un seul nom invalide fait **échouer tout le sync**, donc `ModdyCommandTranslator`
vérifie chaque valeur avant de la renvoyer (et logge un warning en cas de rejet,
sans lever d'exception) :

| Élément | Règle |
|---|---|
| Nom de commande / groupe / paramètre | 1–32 caractères, **minuscules**, regex `^[-_\p{L}\p{N}\p{sc=Deva}\p{sc=Thai}]+$` — pas d'espace, pas d'apostrophe |
| Description | 1–100 caractères (tronquée au besoin) |
| Nom de menu contextuel | 1–32 caractères, espaces et majuscules autorisés |
| Unicité | Deux commandes ne peuvent pas partager le même nom localisé dans une même langue |

Les mots composés utilisent donc un tiret : `effacer-rôles-sauvegardés`,
`mentett-rangok-törlése`, `mis-casos`.

---

## Ajouter / modifier une traduction

1. Ajouter la commande dans `locales/commands/en-US.json` (fichier de
   référence : il définit la liste des clés attendues).
2. Répercuter la clé dans les 31 autres fichiers.
3. Lancer la validation :

   ```bash
   pip install -r requirements-dev.txt
   pytest tests/test_command_localizations.py
   ```

4. Redémarrer le bot (le sync applique les nouvelles localisations).

Les tests vérifient : locales connues, validité des noms, longueur des
descriptions, unicité des noms (top-level et par groupe), unicité des noms de
paramètres au sein d'une commande, et parité des clés avec `en-US.json`.

### Nouvelle commande

Rien à faire de spécial dans le cog : il suffit d'ajouter la clé dans les
fichiers de locale. Tant qu'elle n'y est pas, la commande s'affiche en anglais
partout — aucun crash.

---

## Ce qui n'est PAS localisé

- **Commandes staff** (`/dev`, `/team`, `/mod`, `/manage`, …) : usage interne,
  anglais uniquement.
- **`/testerror`** : commande de développement.
- **Choix de `/translate`** : noms de langues en endonyme, universels.
- **Le contenu des réponses** : géré par `utils/i18n.py` (`locales/fr.json`,
  `locales/en-US.json`), qui suit `interaction.locale` à l'exécution.

---

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Les noms restent en anglais | Pas de re-sync depuis la modif, ou fichier de locale mal nommé |
| `Command localizations loaded for 0 locales` au démarrage | Dossier `locales/commands/` absent du déploiement |
| Warning `Invalid localized command name` | Nom avec espace / majuscule / apostrophe → corriger et relancer les tests |
| Sync global en échec (400) | Deux commandes avec le même nom localisé dans une langue → `pytest tests/test_command_localizations.py` |

---

**Documentation créée le** : 5 août 2026
