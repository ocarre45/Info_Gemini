import os
import datetime
import requests
import docx
import base64

# 1. Clés d'API
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BREVO_KEY = os.getenv("BREVO_API_KEY")
EMAIL_SENDER = "votre-email@domaine.com"  # ⚠️ REMPLACEZ PAR VOTRE EMAIL BREVO VALIDÉ

if not GEMINI_KEY or not BREVO_KEY:
    raise ValueError("GEMINI_API_KEY ou BREVO_API_KEY est manquante.")

# 2. Prompt
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

# 3. Requête HTTP vers le modèle Gemini Flash le plus récent (gemini-2.5-flash / gemini-flash-latest)
gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"

payload_gemini = {
    "contents": [{"parts": [{"text": PROMPT}]}],
    "tools": [{"google_search": {}}]
}

response = requests.post(gemini_url, json=payload_gemini)
res_json = response.json()

# En cas de problème de version, tenter avec l'alias générique 'gemini-flash-latest'
if response.status_code != 200:
    print("Tentative avec gemini-2.5-flash échouée :", res_json)
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
    response = requests.post(gemini_url, json=payload_gemini)
    res_json = response.json()

try:
    texte_veille = res_json['candidates'][0]['content']['parts'][0]['text']
except (KeyError, IndexError):
    raise Exception(f"Impossible de lire la réponse Gemini : {res_json}")

# 4. Génération du fichier Word (.docx)
doc = docx.Document()
doc.add_heading("Veille Logement Social", level=1)

for paragraph in texte_veille.split('\n\n'):
    doc.add_paragraph(paragraph)

today_str = datetime.date.today().strftime('%d/%m/%Y')
filename = f"Veille_Logement_Social_{datetime.date.today()}.docx"
doc.save(filename)

# 5. Envoi par e-mail via Brevo
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
print("Statut Brevo :", res_brevo.status_code, res_brevo.text)

if res_brevo.status_code >= 400:
    raise Exception(f"Erreur Brevo : {res_brevo.text}")
