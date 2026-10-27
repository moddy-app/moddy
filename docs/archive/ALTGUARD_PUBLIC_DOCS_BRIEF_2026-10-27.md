# Brief pour l'agent de la documentation publique — AltGuard (version 2026-10-27)

> **Destinataire** : l'agent Claude Code qui maintient la documentation publique
> de Moddy (`docs.moddy.app`, et la page de données `moddy.app/AltGuard-data`).
> **Source** : le bot `moddy-app/moddy`, PR [moddy-app/moddy#433](https://github.com/moddy-app/moddy/pull/433).
> Ce document dit **ce qui a changé pour les utilisateurs et les administrateurs**
> et **ce qu'il faut mettre à jour** dans la documentation publique. Le détail
> technique côté bot est dans [docs/features/ALTGUARD.md](../features/ALTGUARD.md).

---

## 0. Rappel : ce qu'est AltGuard

AltGuard est une barrière de vérification : un membre qui rejoint un serveur
reste derrière un rôle « non vérifié » tant que le service AltGuard n'a pas
vérifié qu'il n'amène pas un second compte. Le membre clique sur un bouton dans
le salon de vérification, accepte une notice de consentement, puis ouvre un lien
personnel (valable 20 minutes, à usage unique) qui l'amène sur
`verify.moddy.app`. Le bot reçoit ensuite un verdict : **vérifié**, **en
attente** (vérification par le staff) ou **refusé**.

---

## 1. Pages à mettre à jour

| Page | Ce qui change | Priorité |
|---|---|---|
| Page « Données collectées » (`moddy.app/AltGuard-data`) | Nouvelles données collectées, nouvelles durées de conservation, droits, retrait du consentement (§2) | **Obligatoire** (conformité) |
| Article « Se faire vérifier » / parcours membre | Texte du consentement, écran d'autorisation Discord, VPN refusés (§3, §4) | **Obligatoire** |
| Article pour les administrateurs (configuration AltGuard) | Permission *Gérer le serveur* recommandée, libellés lisibles dans le salon de logs (§5, §6) | Recommandé |
| FAQ / dépannage | « Ma vérification n'aboutit pas » → VPN (§4) | Recommandé |

**Lien « Données collectées »** : dans le modal de consentement, ce lien pointe
désormais vers **`https://verify.moddy.app/privacy`** (la notice officielle
publiée par AltGuard) au lieu de `https://moddy.app/AltGuard-data`. Le panneau de
vérification du salon pointe aussi vers cette URL. Deux options :
- soit `moddy.app/AltGuard-data` reprend la notice ci-dessous (§2) et reste à jour ;
- soit elle redirige vers `verify.moddy.app/privacy`.

Ne pas laisser l'ancienne page avec l'ancienne liste de données : elle serait
fausse.

---

## 2. Données collectées (notice officielle, version `2026-10-27`)

Texte fourni par l'exploitant d'AltGuard. **Reprendre le fond tel quel** (les
données, finalités et durées ne doivent pas être reformulées au point d'en
changer le sens). La version du consentement enregistrée par le service est
`2026-10-27`.

### Nouveautés par rapport à la version précédente

- **Modèle de téléphone** ajouté aux caractéristiques de l'appareil.
- **Adresse IP publique révélée par WebRTC** lorsque la connexion passe par un VPN.
- **Comptes tiers liés** au compte Discord (Steam, Xbox, GitHub…) — nouveau
  scope Discord `connections`.
- **Le compte qui a invité le membre** sur le serveur (fourni par le bot).
- **Preuve du consentement** désormais décrite explicitement (3 ans).
- Section **droits** et **retrait du consentement** détaillée.

### Texte — français

**Vérification AltGuard — données et confidentialité**

AltGuard vérifie, à l'entrée d'un serveur Discord, qu'un même utilisateur n'y amène pas plusieurs comptes. Cette page décrit ce qui est collecté à cette occasion, pourquoi, combien de temps c'est conservé, et comment exercer vos droits.

**Ce qui est collecté**

| Donnée | À quoi elle sert | Conservation |
|---|---|---|
| Caractéristiques techniques du navigateur et de l'appareil (dont le modèle de téléphone) et cookie technique | Reconnaître un appareil ou un navigateur déjà vu, pour détecter un second compte amené sur le même serveur. | 18 mois d'inactivité |
| Adresse email (via Discord) | Reconnaître un compte réutilisant la même boîte mail. Conservée chiffrée, jamais lisible sauf sur demande légitime et tracée. | 18 mois d'inactivité |
| Adresse IP, y compris l'adresse publique révélée par WebRTC lorsque la connexion passe par un VPN | Signal faible de corrélation entre comptes. Jamais conservée en clair, seulement sous forme d'empreinte non réversible. | 90 jours |
| Liste de vos serveurs Discord (via Discord) | Mesurer le recouvrement de serveurs entre deux comptes. Conservée uniquement sous forme d'empreintes non réversibles, jamais d'identifiants de serveur lisibles. | 180 jours |
| Comptes tiers liés à votre compte Discord (Steam, Xbox, GitHub…, via Discord) | Reconnaître deux comptes Discord liés au même compte tiers. Seuls les comptes vérifiés sont lus, conservés sous forme d'empreintes non réversibles, jamais d'identifiants lisibles. | 180 jours |
| Le compte qui vous a invité sur le serveur (via le bot) | Corroborer un lien entre deux comptes, jamais à lui seul. Identifiant Discord public, conservé avec votre appartenance au serveur. | 30 jours après le départ du serveur |
| Preuve du consentement (date et version de la politique) | Prouver que la collecte a bien été autorisée. Conservée séparément des données elles-mêmes. | 3 ans |

**Finalité** — La seule finalité est la détection de comptes multiples amenés sur un même serveur. Aucune donnée n'est revendue ni utilisée à des fins publicitaires. Les caractéristiques collectées sont immédiatement transformées en empreintes non réversibles : le serveur Discord et son bot ne reçoivent jamais qu'un verdict (vérifié, en attente, refusé) et un score, jamais vos données.

**Vos droits**
- Accès : obtenir la liste de ce qui est conservé à votre sujet.
- Effacement : demander la suppression de vos données de vérification.
- Retrait du consentement : à tout moment, sans avoir à vous justifier.
- Rectification : l'essentiel des données étant des empreintes non réversibles, la rectification passe le plus souvent par un effacement suivi d'une nouvelle vérification.

**Retirer votre consentement** — Vous pouvez retirer votre consentement et demander l'effacement de vos données de vérification à tout moment. Le retrait n'affecte pas la validité de la collecte déjà effectuée, mais entraîne la suppression de vos empreintes et de vos appartenances vérifiées. La seule trace conservée est la preuve du consentement lui-même, gardée à titre probatoire pour la durée indiquée ci-dessus.

Pour exercer l'un de ces droits, contactez le staff du serveur concerné, qui transmet la demande à l'exploitant du service.

### Texte — anglais

**AltGuard verification — data and privacy**

AltGuard checks, when you join a Discord server, that a single person is not bringing several accounts onto it. This page describes what is collected for that, why, how long it is kept, and how to exercise your rights.

**What is collected**

| Data | Purpose | Retention |
|---|---|---|
| Technical characteristics of the browser and device (including the phone model) and a technical cookie | Recognise a device or browser already seen, to detect a second account brought to the same server. | 18 months of inactivity |
| Email address (via Discord) | Recognise an account reusing the same mailbox. Stored encrypted, never readable except on a traced, legitimate request. | 18 months of inactivity |
| IP address, including the public address revealed by WebRTC when the connection goes through a VPN | Weak correlation signal between accounts. Never stored readable, only as a non-reversible fingerprint. | 90 days |
| List of your Discord servers (via Discord) | Measure server overlap between two accounts. Stored only as non-reversible fingerprints, never as readable server IDs. | 180 days |
| Third-party accounts linked to your Discord account (Steam, Xbox, GitHub…, via Discord) | Recognise two Discord accounts linked to the same third-party account. Only verified accounts are read, stored as non-reversible fingerprints, never as readable identifiers. | 180 days |
| The account that invited you to the server (via the bot) | Corroborate a link between two accounts, never on its own. A public Discord identifier, kept with your server membership. | 30 days after leaving the server |
| Proof of consent (date and policy version) | Prove that collection was authorised. Kept separately from the data itself. | 3 years |

**Purpose** — The only purpose is detecting multiple accounts brought onto the same server. No data is sold or used for advertising. The characteristics collected are immediately turned into non-reversible fingerprints: the Discord server and its bot only ever receive a verdict (verified, pending, denied) and a score, never your data.

**Your rights**
- Access: obtain the list of what is kept about you.
- Erasure: request deletion of your verification data.
- Withdrawal of consent: at any time, without having to justify it.
- Rectification: most data being non-reversible fingerprints, rectification usually means erasure followed by a new verification.

**Withdraw your consent** — You may withdraw your consent and request erasure of your verification data at any time. Withdrawal does not affect the validity of collection already performed, but it removes your fingerprints and your verified memberships. The only trace kept is the proof of consent itself, retained as evidence for the period stated above.

To exercise any of these rights, contact the staff of the server concerned, who forwards the request to the service operator.

---

## 3. Parcours du membre — ce qui change

### Le modal de consentement (texte affiché par le bot)

Même design qu'avant (titre, un paragraphe, trois liens, deux cases à cocher).
Seul le texte a changé. Si la doc montre une capture ou cite le texte, la mettre
à jour.

**Français**
> **Vérification AltGuard**
>
> Pour détecter les comptes multiples, la vérification collecte les caractéristiques de ton navigateur et de ton appareil, un cookie technique, ton adresse e-mail Discord, la liste de tes serveurs, les comptes liés à ton Discord (Steam, Xbox, GitHub…), ton adresse IP et le compte qui t'a invité sur le serveur.
>
> Ces données sont **chiffrées** ou transformées en empreintes non réversibles, utilisées uniquement pour cette détection et conservées pour une durée limitée. Tu peux retirer ton consentement et demander leur effacement à tout moment auprès du staff du serveur.
>
> [Données collectées] · [Conditions d'utilisation] · [Politique de confidentialité]
>
> ☐ J'accepte les conditions d'utilisation et la politique de confidentialité
> ☐ J'accepte le traitement de ces données pour la détection des comptes multiples

**English**
> **AltGuard verification**
>
> To detect multiple accounts, the verification collects your browser and device characteristics, a technical cookie, your Discord email address, your server list, the accounts linked to your Discord (Steam, Xbox, GitHub…), your IP address and the account that invited you to the server.
>
> This data is **encrypted** or turned into non-reversible fingerprints, used only for that detection and kept for a limited time. You can withdraw your consent and request its erasure at any time through the server's staff.
>
> [Collected data] · [Terms of Service] · [Privacy Policy]
>
> ☐ I accept the terms of service and the privacy policy
> ☐ I accept the processing of this data for multi-account detection

Le modal existe aussi en espagnol, portugais (Brésil) et allemand ; il suit la
langue du membre.

### L'écran d'autorisation Discord

Discord demande désormais aussi l'accès aux **comptes liés** (scope
`connections`), en plus de l'identité, de l'adresse e-mail et de la liste des
serveurs. Si la doc décrit ou montre cet écran, ajouter cette ligne et expliquer
pourquoi (§2, « Comptes tiers liés »).

---

## 4. VPN, proxys et relais privés : refusés

**Nouveau comportement** : la vérification est refusée si elle est faite depuis
un VPN, un proxy ou un hébergeur — **y compris le relais privé iCloud et
Cloudflare WARP**.

Ce que vit le membre :
1. La page de vérification affiche « VPN détecté, désactivez-le puis réessayez ».
2. Elle propose un bouton **Réessayer**, qui fonctionne avec le même lien tant
   qu'il n'a pas expiré (20 minutes).
3. Si le lien a expiré, il relance la vérification depuis le bouton du salon de
   vérification, comme d'habitude (5 tentatives maximum par 10 minutes).
4. Tant qu'il n'a pas réessayé sans VPN, il **reste non vérifié** : ce n'est pas
   un refus, aucune décision n'a été prise.

La carte qui donne le lien au membre le prévient désormais :
> FR : La vérification n'est pas possible derrière un VPN ou un proxy (relais privé iCloud et Cloudflare WARP compris). Désactive-le, puis clique sur *Réessayer* dans la page, ou relance la vérification.
>
> EN : Verification isn't possible behind a VPN or proxy (iCloud Private Relay and Cloudflare WARP included). Turn it off, then click *Try again* on the page, or start the verification again.

**Pour les administrateurs** : une tentative bloquée par un VPN ne produit
**aucun** message dans le salon de logs (ce n'est pas un verdict). Un membre qui
« reste bloqué » sans log est très probablement derrière un VPN. Le blocage est
actif par défaut sur tous les serveurs et ne se désactive pas depuis Moddy.

**Entrée de FAQ suggérée** — « Ma vérification n'aboutit pas » : la réponse
ci-dessus (désactiver le VPN / relais privé iCloud / WARP, puis Réessayer ou
relancer).

---

## 5. Pour les administrateurs : l'invitant (permission recommandée)

Moddy transmet désormais à AltGuard **le compte qui a invité le membre**, quand il
peut le déterminer. Un second compte est souvent invité par le lien du compte
principal ; AltGuard s'en sert **uniquement en renfort** d'un autre lien
(appareil, réseau ou compte tiers en commun). Seul, il ne compte pas : inviter
ses amis est la façon normale de rejoindre un serveur.

- Pour que Moddy puisse savoir quelle invitation a été utilisée, il a besoin de
  la permission **Gérer le serveur** (lire la liste des invitations).
- Cette permission est **facultative** : sans elle, AltGuard fonctionne
  normalement, simplement sans cette information. Le module ne la réclame pas
  dans `/config`.
- L'invitant n'est pas déterminé (et donc pas envoyé) quand le membre arrive
  par l'URL personnalisée du serveur, par une invitation à usage unique ou
  supprimée entre-temps, ou quand plusieurs membres arrivent au même instant.

À documenter dans l'article de configuration d'AltGuard, section
« Permissions » : *Gérer les rôles* et *Gérer les salons* obligatoires (inchangé),
*Gérer le serveur* recommandée.

---

## 6. Pour les administrateurs : le salon de logs

Les cartes de verdict du salon de logs (et la commande staff `/mod altguard
refusal`) affichent maintenant chaque signal **avec un libellé lisible** suivi
du code, par exemple : `Même carte graphique (gpu_match)`.

Autres changements visibles :
- La liste des signaux ne contient plus que **ceux qui ont réellement pesé**
  dans le score : elle est plus courte qu'avant.
- La liste des **comptes liés** ne montre plus que les comptes qui pèsent dans la
  décision ; les rapprochements faibles n'apparaissent plus.
- Un signal que Moddy ne connaît pas encore s'affiche avec son code brut :
  ce n'est pas une erreur, AltGuard peut en ajouter.

### Liste des signaux (pour un tableau dans la doc admin)

| Code | Français | English |
|---|---|---|
| `cookie_match` | Même navigateur (cookie AltGuard) | Same browser (AltGuard cookie) |
| `email_match` | Même adresse e-mail | Same email address |
| `connection_match` **(nouveau)** | Même compte tiers lié (Steam, Xbox, GitHub…) | Same linked third-party account (Steam, Xbox, GitHub…) |
| `visitor_match` | Même empreinte de navigateur | Same browser fingerprint |
| `render_match` | Même rendu graphique et audio | Same graphics and audio rendering |
| `gpu_match` | Même carte graphique | Same graphics card |
| `hardware_match` | Même configuration matérielle (écran, processeur, fuseau horaire, modèle) | Same hardware setup (screen, CPU, timezone, model) |
| `fonts_match` | Mêmes polices installées | Same installed fonts |
| `ip_match` | Même adresse IP | Same IP address |
| `ip_prefix_match` | Même sous-réseau | Same subnet |
| `asn_match` | Même opérateur réseau | Same network operator |
| `guild_overlap` | Serveurs Discord en commun | Discord servers in common |
| `guild_overlap_high` | Beaucoup de serveurs Discord en commun | Many Discord servers in common |
| `join_burst` | A rejoint le serveur à moins de 15 min d'écart | Joined the server less than 15 min apart |
| `invited_by` **(nouveau)** | Invité par ce compte (renforce un autre lien) | Invited by this account (reinforces another link) |
| `account_new_24h` | Compte créé il y a moins de 24 h | Account created less than 24 h ago |
| `account_new_7d` | Compte créé il y a moins de 7 jours | Account created less than 7 days ago |
| `account_new_30d` | Compte créé il y a moins de 30 jours | Account created less than 30 days ago |
| `email_unverified` | E-mail non vérifié sur Discord | Email not verified on Discord |
| `email_disposable` | E-mail jetable | Disposable email |
| `default_avatar` | Avatar par défaut | Default avatar |
| `no_badge` | Aucun badge | No badge |
| `datacenter_ip` | Connexion depuis un hébergeur, un cloud ou un VPN | Connection from a host, cloud or VPN |
| `vpn_detected` **(nouveau)** | VPN démasqué : l'IP réelle derrière a été retrouvée | VPN unmasked: the real IP behind it was found |

À rappeler aux administrateurs : **`invited_by` seul ne justifie jamais une
sanction**, il ne fait que renforcer un autre lien.

---

## 7. Ce qu'il ne faut PAS publier

- **Aucun poids, seuil ni barème** (« +15 points », « score ≥ 30 », seuils de
  verdict…). Publier comment le score se construit revient à expliquer comment
  contourner la détection. La liste des signaux (§6) peut figurer dans la doc
  des administrateurs, pas leurs pondérations.
- Ne jamais suggérer aux administrateurs de montrer le score, les signaux ou les
  comptes liés au membre vérifié.
- Ne pas présenter l'absence de verdict (cas du VPN) comme un refus.

---

## 8. Ce qui ne change pas

- Le parcours général : bouton → consentement → lien personnel (20 min, usage
  unique, envoyé en privé) → verdict.
- Les trois verdicts (vérifié, en attente, refusé) et ce qu'ils déclenchent.
- Le mode observation (verdict journalisé, aucune action appliquée).
- La configuration dans `/config` (salon, rôle non vérifié, rôle vérifié, salon
  de logs) et les commandes `/altguard verify` / `/altguard unverify`.
- Le message privé envoyé au membre à la fin de la vérification (il ne contient
  jamais ni score ni signaux).

---

## Checklist pour l'agent de la doc

- [ ] Page « Données collectées » : notice complète FR + EN (§2), ou redirection vers `https://verify.moddy.app/privacy`.
- [ ] Parcours membre : nouveau texte du consentement (§3), scope « comptes liés » sur l'écran Discord.
- [ ] Section VPN / proxy / relais privé iCloud / WARP + entrée de FAQ (§4).
- [ ] Doc admin : permission *Gérer le serveur* recommandée et pourquoi (§5).
- [ ] Doc admin : libellés lisibles dans les logs + tableau des signaux (§6), avec l'avertissement sur `invited_by`.
- [ ] Vérifier qu'aucune page ne publie de pondérations ou de seuils (§7).
- [ ] Mettre à jour les captures d'écran du modal et de la carte du lien si la doc en contient.
