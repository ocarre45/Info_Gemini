import os
import datetime
import requests
from google import genai
from google.genai import types

# 1. Vérification des clés d'environnement
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BREVO_KEY = os.getenv("BREVO_API_KEY")
EMAIL_SENDER = "votre-email@domaine.com"  # ⚠️ Votre e-mail d'expéditeur validé dans Brevo

if not GEMINI_KEY or not BREVO_KEY:
    raise ValueError("GEMINI_API_KEY ou BREVO_API_KEY est manquante.")

# 2. Configuration du client Gemini
client = genai.Client(api_key=GEMINI_KEY)

# 3. Prompt de la veille
PROMPT = """Rôle et objectif
Tu es un analyste spécialisé en politiques publiques et dynamiques de terrain du logement social et abordable. Tu produis une veille de presse quotidienne destinée à un expert du secteur. Appuie-toi sur Google Search pour ne citer que des sources réelles, récentes et vérifiables : n'invente jamais un article, une date ou un lien.

Périmètre géographique et thématique
- Pays : France, Allemagne, Italie.
- Sujet : Logement social et abordable au sens large (macro-politiques ET vie locale/terrain).
  • France : HLM, logement abordable/intermédiaire, bailleurs sociaux, financements (RLS, PLAI/PLUS/PLS, Action Logement, CDC), arbitrages maires/préfets/collectivités, décisions judiciaires/expulsions, tensions de terrain, vie des organismes, santé financière, associations de locataires, précarité énergétique.
  • Allemagne : sozialer Wohnungsbau, Sozialwohnungen, geförderter Wohnraum, Wohnraumförderung, kommunale/genossenschaftliche Wohnungsunternehmen, décisions des Länder et communes, initiatives syndicales (DGB/Mieterbund), reconversions, conflits d'usage.
  • Italie : edilizia residenziale pubblica (ERP), case popolari, housing sociale, canone calmierato, bandi régionaux/municipaux (ALER, ATER), syndicats de locataires (SUNIA, Unione Inquilini), expulsions (sfratti), réhabilitations et initiatives locales.

Sélection et signaux faibles
- Ne te limite PAS aux annonces ministérielles ou aux grandes lois. Recherche activement les SIGNAUX FAIBLES et la PRESSE RÉGIONALE/LOCALE.
- Sélectionne jusqu'à 20 informations au total.
- Fenêtre temporelle : 24 dernières heures.

Structure et mise en forme HTML :
Génère le texte directement formaté en HTML simple (utilises <h2>, <h3>, <ul>, <li>, <b>, <a href="...">) pour qu'il s'affiche parfaitement dans un e-mail. Ne mets pas de balises ```html ou ``` autour.
"""

# 4. Appel de l'API Gemini
models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
texte_html = None

for model_name in models_to_try:
    try:
        print(f"Tentative de génération avec {model_name}...")
        response = client.models.generate_content(
            model=model_name,
            contents=PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        if response and response.text:
            texte_html = response.text
            print(f"Génération réussie avec {model_name} !")
            break
    except Exception as e:
        print(f"Échec avec {model_name}: {e}")

if not texte_html:
    raise RuntimeError("Impossible de générer la veille avec les modèles disponibles.")

# Nettoyage si Gemini ajoute des balises de code Markdown
texte_html = texte_html.replace("```html", "").replace("```", "").strip()

# 5. Envoi de l'e-mail via Brevo (texte dans le corps)
today_str = datetime.date.today().strftime('%d/%m/%Y')

brevo_url = "https://api.brevo.com/v3/smtp/email"
headers_brevo = {
    "accept": "application/json",
    "api-key": BREVO_KEY,
    "content-type": "application/json"
}

payload_brevo = {
    "sender": {"name": "Veille Logement", "email": EMAIL_SENDER},
    "to": [{"email": EMAIL_SENDER}],
    "subject": f"Actualité du Logement Social au {today_str}",
    "htmlContent": f"<div>{texte_html}</div>"
}

res_brevo = requests.post(brevo_url, json=payload_brevo, headers=headers_brevo)
print("Statut d'envoi Brevo :", res_brevo.status_code, res_brevo.text)

if res_brevo.status_code >= 400:
    raise RuntimeError(f"Erreur d'envoi Brevo : {res_brevo.text}")
