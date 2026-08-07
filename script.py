import os
import datetime
import requests
import google.generativeai as genai
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT

# 1. Configuration de l'API Gemini
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BREVO_KEY = os.getenv("BREVO_API_KEY")

genai.configure(api_key=GEMINI_KEY)

# Prompt exact défini ensemble
PROMPT = """
Rôle et objectif : Tu es un analyste spécialisé en politiques publiques et dynamiques de terrain du logement social et abordable...
[Mettez ici l'intégralité du prompt qu'on a validé]
"""

# 2. Exécution de Gemini avec recherche Google
model = genai.GenerativeModel(
    model_name="gemini-1.5-pro",
    tools=[{"google_search": {}}]
)

response = model.generate_content(PROMPT)
texte_veille = response.text

# 3. Génération du fichier Word (.docx)
doc = docx.Document()
doc.add_heading("Veille Logement Social", level=1)
doc.add_paragraph(texte_veille)

filename = f"Veille_Logement_Social_{datetime.date.today()}.docx"
doc.save(filename)

# 4. Envoi de l'email via l'API Brevo (à la liste ListeOC)
brevo_url = "https://api.brevo.com/v3/smtp/email"
headers = {
    "accept": "application/json",
    "api-key": BREVO_KEY,
    "content-type": "application/json"
}

# Envoi du fichier généré
with open(filename, "rb") as f:
    import base64
    encoded_file = base64.b64encode(f.read()).decode("utf-8")

payload = {
    "sender": {"name": "Veille Logement", "email": "ocfr@yahoo.fr"},
    # Pour envoyer directement à un ID de liste Brevo :
    "templateId": 1, # ID d'un template Brevo si vous en utilisez un, ou utilisez le corps HTML
    "htmlContent": f"<h3>Bonjour,</h3><p>Voici la veille du jour en pièce jointe.</p><br/><pre>{texte_veille}</pre>",
    "subject": f"Actualité du Logement Social au {datetime.date.today().strftime('%d/%m/%Y')}",
    "attachment": [
        {
            "content": encoded_file,
            "name": filename
        }
    ]
}

# Si vous voulez l'envoyer aux abonnés d'une liste Brevo spécifique :
# Vous pouvez configurer une Campagne Brevo via API ou envoyer aux destinataires de la liste.

requests.post(brevo_url, json=payload, headers=headers)
print("Veille générée et envoyée avec succès !")
