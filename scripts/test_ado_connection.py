import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

ORG = os.getenv("ADO_ORG")
PROJECT = os.getenv("ADO_PROJECT")
PAT = os.getenv("ADO_PAT")

def get_headers(pat: str) -> dict:
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json"
    }

def main():
    # Comprobación rápida
    if not ORG or not PROJECT or not PAT:
        print("❌ Faltan variables en .env (ADO_ORG / ADO_PROJECT / ADO_PAT)")
        print("ORG:", ORG)
        print("PROJECT:", PROJECT)
        print("PAT existe:", bool(PAT))
        return

    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/wiql?api-version=7.1-preview.2"

    wiql = {
        "query": f"""
    SELECT [System.Id]
    FROM WorkItems
    WHERE [System.TeamProject] = '{PROJECT}'
      AND [System.State] <> 'Closed'
      AND [System.ChangedDate] >= @Today - 180
    ORDER BY [System.ChangedDate] DESC
    """
}

    r = requests.post(url, headers=get_headers(PAT), json=wiql, timeout=30)

    if r.status_code != 200:
        print("❌ Error conectando a Azure DevOps")
        print("Status:", r.status_code)
        print(r.text)
        return

    data = r.json()
    items = data.get("workItems", [])

    print("✅ Conexión correcta con Azure DevOps")
    print("Work items encontrados:", len(items))
    print("Primeros 5 IDs:", [x["id"] for x in items[:5]])

if __name__ == "__main__":
    main()
