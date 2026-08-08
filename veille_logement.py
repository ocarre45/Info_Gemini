#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veille quotidienne « logement social et abordable » (France / Allemagne / Italie).

Chaîne : Gemini (generateContent + Google Search grounding) -> HTML -> Brevo.

Variables d'environnement requises :
    GEMINI_API_KEY   clé Google AI Studio
    BREVO_API_KEY    clé API v3 Brevo

Variables optionnelles :
    SENDER_EMAIL     expéditeur (défaut : veille@oliviercarre.fr)
    SENDER_NAME      nom d'expéditeur
    BREVO_LIST_ID    id de la liste Brevo (défaut : 3)
    REPLY_TO         adresse de réponse
    GEMINI_MODEL     surcharge du modèle
    DRY_RUN          "1" = génère et écrit veille.html sans rien envoyer
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

LOG = logging.getLogger("veille")

PARIS = ZoneInfo("Europe/Paris")

def env(nom: str, defaut: str = "") -> str:
    """
    Une variable définie mais vide vaut absente.

    GitHub Actions substitue une chaîne vide à `${{ vars.X }}` quand la variable
    de dépôt n'existe pas : `os.getenv(nom, defaut)` renverrait alors "" et non
    le défaut, puisque la variable est bien présente dans l'environnement.
    """
    valeur = os.getenv(nom, "")
    return valeur.strip() or defaut


SENDER_EMAIL = env("SENDER_EMAIL", "ocfr@yahoo.fr")
SENDER_NAME = env("SENDER_NAME", "Veille Logement Social")
REPLY_TO = env("REPLY_TO", SENDER_EMAIL)
LIST_ID_BREVO = int(env("BREVO_LIST_ID", "3"))
DRY_RUN = env("DRY_RUN").lower() in {"1", "true", "yes"}

# Modèle épinglé + repli sur l'alias mouvant si l'ID stable disparaît.
MODELS = [env("GEMINI_MODEL", "gemini-3.6-pro"), "gemini-3.6-flash"]

BREVO_BASE = "https://api.brevo.com/v3"
HTTP_TIMEOUT = 60
MAX_TO_PER_CALL = 99  # limite Brevo pour un envoi transactionnel


SENTINELLE = "<!--DEBUT-->"


def build_prompt(now: datetime) -> str:
    """
    Prompt d'origine, restitué à l'identique.

    Trois ajouts seulement, tous d'ordre mécanique et non éditorial : le bloc de
    datation (le modèle ignore la date d'exécution, sans quoi « 24 dernières
    heures » n'a aucun référent), la sentinelle de tête (bogue de troncature du
    grounding Gemini) et l'interdiction explicite du Markdown (des puces en
    astérisque avaient fui dans le rendu). Seule correction au texte : la graphie
    italienne « pubblica », qui figurait en espagnol.
    """
    debut = now - timedelta(hours=24)
    return f"""Rôle et objectif
Tu es un analyste spécialisé en politiques publiques et dynamiques de terrain du logement social et abordable. Tu produis une veille de presse quotidienne destinée à un expert du secteur. Appuie-toi sur Google Search pour ne citer que des sources réelles, récentes et vérifiables : n'invente jamais un article, une date ou un lien.

Périmètre géographique et thématique
- Pays : France, Allemagne, Italie.
- Sujet : Logement social et abordable au sens large (macro-politiques ET vie locale/terrain).
 • France : HLM, logement abordable/intermédiaire, bailleurs sociaux, financements (RLS, PLAI/PLUS/PLS, Action Logement, CDC), arbitrages maires/préfets/collectivités, décisions judiciaires/expulsions, tensions de terrain, vie des organismes, santé financière, associations de locataires, précarité énergétique.
 • Allemagne : sozialer Wohnungsbau, Sozialwohnungen, geförderter Wohnraum, Wohnraumförderung, kommunale/genossenschaftliche Wohnungsunternehmen, décisions des Länder et communes, initiatives syndicales (DGB/Mieterbund), reconversions, conflits d'usage.
 • Italie : edilizia residenziale pubblica (ERP), case popolari, housing sociale, canone calmierato, bandi régionaux/municipaux (ALER, ATER), syndicats de locataires (SUNIA, Unione Inquilini), expulsions (sfratti), réhabilitations et initiatives locales.

Sélection et signaux faibles
- Ne te limite PAS aux annonces ministérielles ou aux grandes lois. Recherche activement les SIGNAUX FAIBLES et la PRESSE RÉGIONALE/LOCALE.
- Formule tes requêtes de recherche dans la langue du pays traité : en allemand pour l'Allemagne, en italien pour l'Italie, jamais en français. Lis les articles dans leur langue d'origine et restitue-les en français. Croise les termes du périmètre ci-dessus avec des noms de villes, de Länder, de régions et d'organismes locaux.
- Sélectionne jusqu'à 20 informations au total.
- Fenêtre temporelle : 24 dernières heures. Nous sommes le {now.strftime('%d/%m/%Y')} à {now.strftime('%Hh%M')} (heure de Paris) : ne retiens que ce qui a été publié après le {debut.strftime('%d/%m/%Y %Hh%M')}.

Structure et mise en forme HTML :
Ta réponse commence exactement par {SENTINELLE} et par rien d'autre : aucun préambule, aucun espace avant.
Génère ensuite le texte directement formaté en HTML simple (utilise <h2>, <h3>, <ul>, <li>, <b>, <a href="...">) pour qu'il s'affiche parfaitement dans un e-mail. Ne mets pas de balises ```html ou ``` autour. N'utilise aucun Markdown : jamais de * ni de # pour structurer. Chaque balise <a> doit être complète et sur une seule ligne, avec l'URL entière entre guillemets doubles.
"""


# --------------------------------------------------------------------------- #
# Étape 1 — génération
# --------------------------------------------------------------------------- #

FENCE_RE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)
MARKDOWN_PUCE_RE = re.compile(r"^[ \t]*[\*\-\u2022]+[ \t]+", re.MULTILINE)


def strip_fences(text: str) -> str:
    return FENCE_RE.sub("", text).strip()


def extract_text(response) -> str:
    if not response.candidates:
        raise RuntimeError("Réponse sans candidat (probable blocage de sécurité).")
    candidate = response.candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    if finish and str(finish).upper().endswith("MAX_TOKENS"):
        LOG.warning("Réponse tronquée en fin (MAX_TOKENS).")
    parts = getattr(candidate.content, "parts", None) or []
    chunks = [p.text for p in parts if getattr(p, "text", None)]
    if not chunks:
        raise RuntimeError(f"Réponse sans contenu textuel (finish_reason={finish}).")
    return "".join(chunks)


def valider(texte: str) -> str:
    """
    Garde-fou contre la troncature de tête du grounding Gemini.

    Bogue documenté sur les variantes Flash 3.5 et 3.6 : la partie textuelle peut
    arriver amputée de son début, sans que rien dans la réponse ne le signale.
    """
    texte = strip_fences(texte)
    if not texte.startswith(SENTINELLE):
        apercu = texte[:120].replace("\n", " ")
        raise RuntimeError(f"Sentinelle absente — début de réponse perdu. Reçu : « {apercu} »")
    texte = texte[len(SENTINELLE) :].strip()
    if len(texte) < 500:
        raise RuntimeError(f"Contenu anormalement court ({len(texte)} caractères).")
    if MARKDOWN_PUCE_RE.search(texte):
        raise RuntimeError("Puces Markdown détectées : le format HTML n'a pas été respecté.")
    if texte.count("<a ") != texte.count("</a>"):
        raise RuntimeError("Balises <a> déséquilibrées : HTML malformé.")
    return texte


def extract_sources(response) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for candidate in response.candidates or []:
        meta = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                out.append((web.title or web.uri, web.uri))
    seen: set[str] = set()
    return [(t, u) for t, u in out if not (u in seen or seen.add(u))]


def extract_search_entry_point(response) -> str:
    for candidate in response.candidates or []:
        meta = getattr(candidate, "grounding_metadata", None)
        sep = getattr(meta, "search_entry_point", None)
        rendered = getattr(sep, "rendered_content", None)
        if rendered:
            return rendered
    return ""


def generate(now: datetime) -> tuple[str, list[tuple[str, str]], str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.3,
        max_output_tokens=16384,
    )
    prompt = build_prompt(now)
    derniere: Exception | None = None

    for model_name in MODELS:
        for tentative in range(1, 4):
            try:
                LOG.info("Génération : %s (tentative %d)", model_name, tentative)
                response = client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                html = valider(extract_text(response))
                LOG.info("Succès avec %s (%d caractères).", model_name, len(html))
                return html, extract_sources(response), extract_search_entry_point(response)
            except Exception as exc:  # noqa: BLE001
                derniere = exc
                LOG.warning("Échec %s tentative %d : %s", model_name, tentative, exc)
                time.sleep(5 * tentative)

    raise RuntimeError(f"Génération impossible. Dernière erreur : {derniere}")


# --------------------------------------------------------------------------- #
# Étape 2 — mise en forme de l'e-mail
# --------------------------------------------------------------------------- #

def render_email(html: str, sources: list[tuple[str, str]], entry_point: str, now: datetime) -> str:
    bloc_sources = ""
    if sources:
        items = "".join(
            f'<li><a href="{url}" style="color:#003A70;">{titre}</a></li>'
            for titre, url in sources[:40]
        )
        bloc_sources = (
            '<hr style="border:none;border-top:1px solid #d9d9d9;margin:24px 0;">'
            '<h3 style="color:#003A70;font-size:14px;">Sources consultées par le moteur de recherche</h3>'
            f'<ul style="font-size:12px;color:#555;">{items}</ul>'
            '<p style="font-size:11px;color:#888;">Ces liens de redirection Google expirent '
            "environ trente jours après la génération.</p>"
        )

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#1a1a1a;max-width:720px;">
<h1 style="color:#003A70;font-size:20px;border-bottom:3px solid #C8102E;padding-bottom:6px;">
Veille logement social et abordable</h1>
<p style="font-size:12px;color:#666;">France, Allemagne, Italie — {now.strftime('%d/%m/%Y')} à {now.strftime('%Hh%M')}</p>
{html}
{bloc_sources}
{entry_point}
</div>"""


def to_plain_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</(p|li|h[1-6]|div)>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- #
# Étape 3 — envoi Brevo
# --------------------------------------------------------------------------- #

def brevo_headers() -> dict:
    return {
        "accept": "application/json",
        "api-key": os.environ["BREVO_API_KEY"],
        "content-type": "application/json",
    }


def fetch_list_contacts(list_id: int) -> list[dict]:
    """
    /v3/smtp/email n'accepte PAS de champ `listIds` : `to` est obligatoire.
    On résout donc la liste en adresses, en excluant les contacts désinscrits ou blacklistés.
    """
    recipients: list[dict] = []
    offset, limit = 0, 500
    while True:
        response = requests.get(
            f"{BREVO_BASE}/contacts/lists/{list_id}/contacts",
            headers=brevo_headers(),
            params={"limit": limit, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("contacts", [])
        for contact in batch:
            if contact.get("emailBlacklisted"):
                continue
            attrs = contact.get("attributes") or {}
            nom = " ".join(
                str(attrs[k]) for k in ("PRENOM", "FIRSTNAME", "NOM", "LASTNAME") if attrs.get(k)
            ).strip()
            entry = {"email": contact["email"]}
            if nom:
                entry["name"] = nom
            recipients.append(entry)
        offset += limit
        if len(batch) < limit or offset >= payload.get("count", 0):
            break

    if not recipients:
        raise RuntimeError(f"La liste Brevo #{list_id} ne contient aucun destinataire actif.")
    LOG.info("%d destinataire(s) actif(s) dans la liste #%d.", len(recipients), list_id)
    return recipients


def send_email(recipients: list[dict], html: str, now: datetime) -> None:
    sujet = f"Veille logement social — {now.strftime('%d/%m/%Y')}"
    for i in range(0, len(recipients), MAX_TO_PER_CALL):
        lot = recipients[i : i + MAX_TO_PER_CALL]
        payload = {
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "to": lot,
            "replyTo": {"email": REPLY_TO},
            "subject": sujet,
            "htmlContent": html,
            "textContent": to_plain_text(html),
            "tags": ["veille-logement"],
            "headers": {"X-Mailin-custom": "source:github-actions"},
        }
        response = requests.post(
            f"{BREVO_BASE}/smtp/email",
            json=payload,
            headers=brevo_headers(),
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Erreur Brevo {response.status_code} : {response.text}")
        LOG.info("Lot de %d destinataire(s) envoyé : %s", len(lot), response.json())


# --------------------------------------------------------------------------- #

def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    manquantes = [k for k in ("GEMINI_API_KEY", "BREVO_API_KEY") if not env(k)]
    if manquantes:
        LOG.error("Variables d'environnement manquantes : %s", ", ".join(manquantes))
        return 2

    now = datetime.now(PARIS)
    corps, sources, entry_point = generate(now)
    html = render_email(corps, sources, entry_point, now)

    if DRY_RUN:
        with open("veille.html", "w", encoding="utf-8") as fh:
            fh.write(html)
        LOG.info("DRY_RUN : contenu écrit dans veille.html, aucun envoi.")
        return 0

    send_email(fetch_list_contacts(LIST_ID_BREVO), html, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
