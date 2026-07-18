"""Cliente da Evolution API (WhatsApp self-hosted).

Cada clínica tem uma `instance_name` única no Evolution.
Operações: criar instância, obter QR Code, status, enviar mensagem.

Docs: https://doc.evolution-api.com/
"""
import httpx

from config import settings


class WhatsAppService:
    def __init__(self):
        self.base_url = settings.EVOLUTION_API_URL.rstrip("/")
        self.api_key = settings.EVOLUTION_API_KEY
        self.timeout = 30

    def _headers(self) -> dict:
        return {"apikey": self.api_key, "Content-Type": "application/json"}

    # ------------------------------------------------------------------ instância

    def criar_instancia(self, instance_name: str) -> dict:
        """Cria nova instância no Evolution. Idempotente: se já existe, retorna ok."""
        url = f"{self.base_url}/instance/create"
        payload = {
            "instanceName": instance_name,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                if resp.status_code in (200, 201):
                    return {"success": True, "data": resp.json()}
                if resp.status_code == 403 and "already" in resp.text.lower():
                    return {"success": True, "data": {"already_exists": True}}
                return {"success": False, "error": resp.text, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def obter_qrcode(self, instance_name: str) -> dict:
        """Retorna QR Code base64 pra clínica escanear no WhatsApp do celular."""
        url = f"{self.base_url}/instance/connect/{instance_name}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "success": True,
                        "base64": data.get("base64") or data.get("qrcode", {}).get("base64"),
                        "pairing_code": data.get("pairingCode"),
                        "raw": data,
                    }
                return {"success": False, "error": resp.text, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def status_instancia(self, instance_name: str) -> dict:
        url = f"{self.base_url}/instance/connectionState/{instance_name}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    state = (data.get("instance") or {}).get("state") or data.get("state")
                    return {"success": True, "conectado": state == "open", "estado": state, "raw": data}
                return {"success": False, "error": resp.text, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def desconectar(self, instance_name: str) -> dict:
        url = f"{self.base_url}/instance/logout/{instance_name}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.delete(url, headers=self._headers())
                return {"success": resp.status_code == 200, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ mensagem

    def enviar_mensagem(self, instance_name: str, telefone: str, mensagem: str) -> dict:
        """Envia texto. Telefone formato BR: 5511999999999 (com DDI)."""
        telefone = telefone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        url = f"{self.base_url}/message/sendText/{instance_name}"
        payload = {"number": telefone, "text": mensagem}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                if resp.status_code in (200, 201):
                    return {"success": True, "data": resp.json()}
                return {"success": False, "error": resp.text, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ webhook

    def configurar_webhook(self, instance_name: str, url_webhook: str) -> dict:
        """Aponta o webhook da instância pra URL pública da Recepia.

        Evolution 2.3.x: payload aninhado em "webhook" com chaves camelCase.
        (A 2.2.x usava o formato plano `webhook_by_events` — incompatível.)
        """
        url = f"{self.base_url}/webhook/set/{instance_name}"
        payload = {
            "webhook": {
                "enabled": True,
                "url": url_webhook,
                "byEvents": False,
                "base64": False,
                # F2: o Evolution reenvia esse header estático em todo callback;
                # a Recepia o valida em _validar_token. Vazio só em DEBUG local.
                "headers": {"X-Webhook-Token": settings.EVOLUTION_WEBHOOK_SECRET},
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            }
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                return {"success": resp.status_code in (200, 201), "raw": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}
