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

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "veille@oliviercarre.fr")
SENDER_NAME = os.getenv("SENDER_NAME", "Veille Logement Social")
REPLY_TO = os.getenv("REPLY_TO", SENDER_EMAIL)
LIST_ID_BREVO = int(os.getenv("BREVO_LIST_ID", "3"))
DRY_RUN = os.getenv("DRY_RUN", "").strip() in {"1", "true", "True"}

# Modèle épinglé + repli sur l'alias mouvant si l'ID stable disparaît.
MODELS = [os.getenv("GEMINI_MODEL", "gemini-3.6-flash"), "gemini-flash-latest"]

BREVO_BASE = "https://api.brevo.com/v3"
HTTP_TIMEOUT = 60
MAX_TO_PER_CALL = 99  # limite Brevo pour un envoi transactionnel


def build_prompt(now: datetime) -> str:
    """Le prompt embarque la date : le modèle n'a aucune notion de « aujourd'hui »."""
    debut = now - timedelta(hours=24)
    return f"""Rôle et objectif
Tu es un analyste spécialisé en politiques publiques et dynamiques de terrain du logement social et abordable. Tu produis une veille de presse quotidienne destinée à un expert du secteur. Appuie-toi systématiquement sur Google Search pour ne citer que des sources réelles, récentes et vérifiables. N'invente jamais un article, une date ou une URL : si tu n'as pas trouvé la source par recherche, ne la cite pas.

Fenêtre temporelle
Nous sommes le {now.strftime('%A %d %B %Y')} à {now.strftime('%Hh%M')} (heure de Paris).
Ne retiens que les informations publiées entre le {debut.strftime('%d/%m/%Y %Hh%M')} et maintenant.
Indique la date de publication de chaque information. Si une information importante est légèrement antérieure à cette fenêtre, tu peux la retenir en la signalant explicitement par la mention « Antérieur à la période revue ».

Périmètre géographique et thématique
- Pays : France, Allemagne, Italie.
- Sujet : logement social et abordable au sens large (macro-politiques ET vie locale / terrain).
  • France : HLM, logement abordable et intermédiaire, bailleurs sociaux, financements (RLS, PLAI/PLUS/PLS, Action Logement, CDC / Banque des Territoires), arbitrages maires / préfets / collectivités, décisions judiciaires et expulsions, tensions de terrain, vie des organismes, santé financière, associations de locataires, précarité énergétique.
  • Allemagne : sozialer Wohnungsbau, Sozialwohnungen, geförderter Wohnraum, Wohnraumförderung, kommunale und genossenschaftliche Wohnungsunternehmen, décisions des Länder et des communes, initiatives syndicales (DGB, Deutscher Mieterbund), reconversions, conflits d'usage.
  • Italie : edilizia residenziale pubblica (ERP), case popolari, housing sociale, canone calmierato, bandi régionaux et municipaux (ALER, ATER), syndicats de locataires (SUNIA, Unione Inquilini), expulsions (sfratti), réhabilitations et initiatives locales.

Sélection et signaux faibles
- Ne te limite pas aux annonces ministérielles ni aux grandes lois. Recherche activement les signaux faibles et la presse régionale et locale.
- Sélectionne jusqu'à 20 informations au total, classées par pays puis par ordre d'importance décroissante.
- Précise toujours la nature exacte des financements évoqués.
- Pas de formules subjectives ni de qualificatifs d'appréciation. N'emploie pas le mot « marché » pour désigner un pays.
- Mentionne le drapeau et le nom du pays une seule fois, en tête de chaque section.

Format de sortie
Produis directement du HTML simple : <h2>, <h3>, <ul>, <li>, <b>, <a href="...">.
Chaque information : titre en <b>, résumé de 2 à 3 phrases, puis source et date sous la forme <i>Source — JJ/MM/AAAA</i> avec un lien <a href> vers l'article.
N'entoure pas la sortie de balises Markdown (```html ou ```). Ne produis ni <html>, ni <head>, ni <body>.
"""


# --------------------------------------------------------------------------- #
# Étape 1 — génération
# --------------------------------------------------------------------------- #

FENCE_RE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)


def strip_fences(text: str) -> str:
    """Retire uniquement les clôtures de bloc en tête et en fin, pas les backticks internes."""
    return FENCE_RE.sub("", text).strip()


def extract_text(response) -> str:
    """`response.text` peut être None (blocage, aucune part texte). On explicite l'échec."""
    if not response.candidates:
        raise RuntimeError("Réponse Gemini sans candidat (probable blocage de sécurité).")

    candidate = response.candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    if finish and str(finish).upper().endswith("MAX_TOKENS"):
        LOG.warning("Réponse tronquée (MAX_TOKENS) — augmenter max_output_tokens.")

    parts = getattr(candidate.content, "parts", None) or []
    chunks = [p.text for p in parts if getattr(p, "text", None)]
    if not chunks:
        raise RuntimeError(f"Réponse Gemini sans contenu textuel (finish_reason={finish}).")
    return "".join(chunks)


def extract_sources(response) -> list[tuple[str, str]]:
    """Sources effectivement consultées par le grounding : le seul garde-fou anti-hallucination."""
    out: list[tuple[str, str]] = []
    for candidate in response.candidates or []:
        meta = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                out.append((web.title or web.uri, web.uri))
    # dédoublonnage en conservant l'ordre
    seen: set[str] = set()
    return [(t, u) for t, u in out if not (u in seen or seen.add(u))]


def extract_search_entry_point(response) -> str:
    """Les conditions d'utilisation du grounding imposent d'afficher les Search Suggestions."""
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
        temperature=0.2,
        max_output_tokens=8192,
    )
    prompt = build_prompt(now)
    last_error: Exception | None = None

    for model_name in MODELS:
        for attempt in range(1, 4):
            try:
                LOG.info("Génération : %s (tentative %d)", model_name, attempt)
                response = client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                html = strip_fences(extract_text(response))
                if len(html) < 400:
                    raise RuntimeError(f"Contenu anormalement court ({len(html)} caractères).")
                LOG.info("Succès avec %s (%d caractères).", model_name, len(html))
                return html, extract_sources(response), extract_search_entry_point(response)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                LOG.warning("Échec %s tentative %d : %s", model_name, attempt, exc)
                time.sleep(5 * attempt)

    raise RuntimeError(f"Génération impossible sur tous les modèles. Dernière erreur : {last_error}")


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

    manquantes = [k for k in ("GEMINI_API_KEY", "BREVO_API_KEY") if not os.getenv(k)]
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
