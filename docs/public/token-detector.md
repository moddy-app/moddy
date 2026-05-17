# Token Detector

Moddy détecte automatiquement les tokens Discord postés dans les messages publics et prévient immédiatement la personne concernée pour qu'elle puisse agir.

---

## C'est quoi un token Discord ?

Quand tu te connectes à Discord, l'application reçoit un code secret — appelé **token** — qui lui permet d'agir à ta place : envoyer des messages, lire tes serveurs, modifier tes paramètres, etc.

**Si quelqu'un d'autre obtient ce code, il peut utiliser ton compte comme s'il était toi** — sans avoir besoin de ton mot de passe, ton email, ou ton code 2FA.

Ça arrive souvent par accident : on copie-colle du code, on partage une capture d'écran sans faire attention, on utilise une mauvaise application…

---

## Ce que Moddy a fait

Dès qu'un token est détecté dans un message, Moddy :

1. **Vérifie qu'il est réel** — il peut s'agir d'un faux positif (une chaîne de caractères qui ressemble à un token sans en être un). Si ce n'est pas un vrai token actif, Moddy l'ignore.
2. **T'envoie un message privé** avec les détails de où le token a été posté, et des boutons pour agir immédiatement.
3. **Garde le token sécurisé** pour que les boutons dans le DM continuent de fonctionner. Il est chiffré (protégé par un verrou numérique) et sera supprimé définitivement dès que tu cliques sur **Invalider le token**, ou automatiquement au bout de 7 jours.

---

## Ce que tu peux faire

### Invalider le token — à faire en premier
Ce bouton dit à Discord d'annuler immédiatement le code qui a été exposé. La session liée à ce token est fermée à l'instant. **Tes autres sessions actives (téléphone, autre navigateur…) ne sont pas touchées** — seulement la session compromise.

Ce bouton fonctionne même si Moddy a redémarré entre-temps.

### Supprimer le message
Tente de supprimer le message qui contenait le token, pour que personne d'autre ne puisse le copier. Moddy essaie d'abord avec ses propres permissions, puis autrement si nécessaire. Si ça ne marche pas, il t'indiquera de le supprimer toi-même.

### Infos sur le message
Affiche les détails complets de là où le token a été détecté : serveur, salon, auteur, date, et une version cachée du message (le token lui-même est masqué).

---

## Ce que tu devrais faire ensuite

Après avoir cliqué sur **Invalider le token**, voici ce qu'on recommande pour être sûr à 100 % :

1. **Change ton mot de passe** sur [discord.com/settings/account](https://discord.com/settings/account) — ça déconnecte **toutes** tes sessions actives d'un coup. C'est le moyen le plus simple de remettre ton compte complètement à l'abri.
2. **Vérifie tes sessions actives** sur [discord.com/settings/sessions](https://discord.com/settings/sessions) et supprime celles que tu ne reconnais pas.
3. **Active la double authentification (2FA)** si ce n'est pas encore fait — ça protège ton compte même si ton mot de passe est un jour compromis.

---

## Alertes pour les bots

Si le token détecté appartient à un **bot Discord**, Moddy prévient le propriétaire du bot (et tous les membres de l'équipe si l'application fait partie d'une équipe). L'alerte inclut un lien direct vers le Portail Développeur pour régénérer le token immédiatement.

Pour les bots, il n'y a pas de bouton "Invalider" — la seule solution est de **régénérer le token dans le Portail Développeur** le plus vite possible.

---

## Confidentialité et sécurité

- **Ton token est chiffré et protégé.** Il est stocké sous forme chiffrée (comme un coffre dont seul Moddy possède la clé). Même si quelqu'un accédait à la base de données, il ne verrait que des données illisibles. Le token est effacé définitivement dès que tu l'invalides, ou après 7 jours automatiquement.
- **Seul le nécessaire est conservé.** Le contenu du message n'est jamais stocké tel quel — seulement une version masquée avec le token remplacé par `[TOKEN REDACTED]`. Les informations de contexte (nom du serveur, du salon, de l'auteur, état des boutons) sont gardées pour que les boutons continuent de fonctionner après un redémarrage du bot.
- **Moddy ne te demandera jamais ton mot de passe, ton token, ou tes identifiants.** Le DM d'alerte contient uniquement des boutons d'action. Si tu reçois un message prétendant venir de Moddy et qui te demande de te connecter ou de donner quoi que ce soit, c'est une arnaque.
- **Les alertes sont envoyées automatiquement**, personne chez Moddy ne les lit ni ne les déclenche manuellement.

---

## Questions fréquentes

**Pourquoi est-ce que j'ai reçu ce DM ?**
Un message posté dans un serveur où Moddy est actif contenait une chaîne de caractères correspondant au format d'un token Discord. Moddy a vérifié auprès de l'API Discord que c'était bien un token actif lié à ton compte — ce n'est pas une fausse alerte.

**Les boutons affichent "Action non disponible" — que faire ?**
Cela signifie que 7 jours se sont écoulés depuis l'alerte et que les données ont été automatiquement supprimées. C'est rare. Si tu penses que ton compte est toujours à risque, change ton mot de passe sur [discord.com/settings/account](https://discord.com/settings/account) — c'est de toute façon l'action la plus efficace.

**C'est du phishing ?**
Non. Le message vient du compte Discord officiel de Moddy. Tu peux vérifier l'expéditeur en consultant le profil du bot. Moddy ne te demandera **jamais** de te connecter via un lien, de donner ton mot de passe, ou de partager quoi que ce soit.

**Je dois paniquer ?**
Non. Reçevoir cette alerte rapidement, c'est une bonne chose — tu as l'occasion d'agir avant que quoi que ce soit de grave arrive. Invalide le token, change ton mot de passe, et tout ira bien.

**Est-ce que je peux désactiver cette fonction ?**
La détection de tokens est une fonctionnalité de sécurité et ne peut pas être désactivée par les membres d'un serveur. Les administrateurs de serveur qui ont des questions spécifiques peuvent contacter l'équipe Moddy via le serveur de support.

---

## Besoin d'aide ?

Rejoins notre [serveur de support](https://moddy.app/support) si tu as des questions ou si quelque chose ne fonctionne pas comme prévu.
