"""
Système Incognito pour les commandes slash de Moddy
Permet aux utilisateurs de contrôler la visibilité de leurs réponses
"""

import nextcord
from nextcord import app_commands
from typing import Optional
import functools


def add_incognito_option(default_value: bool = True):
    """
    Décorateur qui ajoute l'option incognito à une commande slash

    Args:
        default_value: Valeur par défaut si pas de préférence utilisateur
    """

    def decorator(func):
        # Wrapper qui ajoute le paramètre incognito
        @functools.wraps(func)
        async def wrapper(self, interaction: nextcord.Interaction, *args, incognito: Optional[bool] = None, **kwargs):
            # IMPORTANT : Si incognito n'est pas spécifié explicitement dans la commande
            # on vérifie la préférence utilisateur
            if incognito is None:
                # Vérifie d'abord l'attribut utilisateur pour la préférence par défaut
                if hasattr(self, 'bot') and self.bot.db:
                    try:
                        # Récupère la préférence DEFAULT_INCOGNITO
                        user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')

                        # Si l'utilisateur a une préférence définie
                        if user_pref is not None:
                            # Si DEFAULT_INCOGNITO est False, on veut que les messages soient publics par défaut
                            incognito = user_pref
                        else:
                            # Pas de préférence définie, on utilise la valeur par défaut
                            incognito = default_value
                    except Exception as e:
                        # En cas d'erreur, on utilise la valeur par défaut
                        import logging
                        logger = logging.getLogger('moddy')
                        logger.error(f"Erreur récupération préférence incognito: {e}")
                        incognito = default_value
                else:
                    incognito = default_value

            # Stocke la valeur incognito dans l'interaction pour que la commande puisse l'utiliser
            interaction.extras = getattr(interaction, 'extras', {})
            interaction.extras['incognito'] = incognito

            # Appelle la fonction originale
            return await func(self, interaction, *args, **kwargs)

        # Ajoute le paramètre incognito aux annotations
        wrapper.__annotations__ = func.__annotations__.copy()
        wrapper.__annotations__['incognito'] = Optional[bool]

        return wrapper

    return decorator


def get_incognito_setting(interaction: nextcord.Interaction) -> bool:
    """
    Récupère le réglage incognito depuis l'interaction

    Args:
        interaction: L'interaction Discord

    Returns:
        bool: True si ephemeral (privé), False si public
    """
    return interaction.extras.get('incognito', True) if hasattr(interaction, 'extras') else True


# Export des fonctions principales
__all__ = ['add_incognito_option', 'get_incognito_setting']