import os
import datetime
import base64
import requests
import docx
from google import genai
from google.genai import types

# 1. Verification des clés d'environnement
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BREVO_KEY = os.getenv("BREVO_API_KEY")
EMAIL_SENDER = "ocfr@yahoo.fr"  # ⚠️ Remplacez par votre e-mail d'expéditeur validé dans Brevo

if not GEMINI_KEY or not BREVO_KEY:
    raise ValueError("Les variables d'environnement GEMINI_API_KEY ou BREVO_API_KEY sont manquantes.")

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

Structure et mise en forme (Format compatible Word) :
# Actualité du Logement Social au [Date du jour]
**Heure de création :** [Heure actuelle] | **Périmètre :** France, Allemagne, Italie | **Informations retenues :** [Nombre total]

---

# 🇫🇷 France
- **Titre :** [Titre]
- **Synthèse :** [Résumé factuel]
- **Source :** [Nom], [Date] — [URL]

# 🇩🇪 Allemagne
[Même structure]

# 🇮🇹 Italie
[Même structure]

---
**Bilan de la veille :**
- Total d'items : [X]
- Pays sans actualité retenue : [Noms]
"""

# 4. Appel de l'API Gemini avec Google Search Grounding
# Modèles essayés successivement en cas de mise à jour des versions
models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
texte_veille = None

for model_name in models_to_try:
    try:
        print(f"Tentative de génération avec le modèle : {model_name}")
        response = client.models.generate_content(
            model=model_name,
            contents=PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        if response and response.text:
            texte_veille = response.text
            print(f"Génération réussie avec {model_name} !")
            break
    except Exception as e:
        print(f"Échec avec {model_name}: {e}")

if not texte_veille:
    raise RuntimeError("Impossible de générer le contenu de la veille avec les modèles Gemini disponibles.")

# 5. Création du fichier Word (.docx)
doc = docx.Document()
doc.add_heading("Veille Logement Social", level=1)

for paragraph in texte_veille.split('\n\n'):
    doc.add_paragraph(paragraph)

today_str = datetime.date.today().strftime('%d/%m/%Y')
filename = f"Veille_Logement_Social_{datetime.date.today()}.docx"
doc.save(filename)
print(f"Document Word enregistré sous : {filename}")

# 6. Envoi de l'e-mail via Brevo API REST
with open(filename, "rb") as f:
    encoded_file = base64.b64encode(f.read()).decode("utf-8")

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
    "htmlContent": f"<h3>Bonjour,</h3><p>Voici votre veille quotidienne du {today_str} en pièce jointe.</p>",
    "attachment": [
        {
            "content": encoded_file,
            "name": filename
        }
    ]
}

res_brevo = requests.post(brevo_url, json=payload_brevo, headers=headers_brevo)
print("Statut d'envoi Brevo :", res_brevo.status_code, res_brevo.text)

if res_brevo.status_code >= 400:
    raise RuntimeError(f"Erreur d'envoi Brevo : {res_brevo.text}")
