"""Orquestrador de mensagens automáticas — multi-tenant, ORM puro.

Responsabilidades:
- Buscar agendamentos próximos pra disparar confirmação
- Enviar mensagens via WhatsAppService (Evolution)
- Receber resposta do paciente (via webhook) e classificar com Groq
- Atualizar status do agendamento + enviar resposta apropriada
- G1: gerenciar fluxo multi-step de reagendamento com slots reais
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from core.timezones import agora_utc, from_utc_to_br
from models import (
    Agendamento, Clinica, Configuracao, EstadoConversa, FluxoConversa,
    Interacao, Paciente, Status, TipoInteracao,
)
from services.processor import (
    IAProcessor, INTENCAO_REAGENDAR, INTENCAO_NAO_ENTENDIDO, INTENCAO_OPT_OUT,
)
from services.slots import (
    extrair_numero_resposta, formatar_opcoes_pra_mensagem, sugerir_slots,
)
from services.whatsapp import WhatsAppService

log = logging.getLogger("recepia.scheduler")


class SchedulerService:
    def __init__(self, db: Session):
        self.db = db
        self.whatsapp = WhatsAppService()
        self.processor = IAProcessor()

    # ========================================================================
    # BUSCAS
    # ========================================================================

    def buscar_agendamentos_pra_confirmar(self, clinica_id: Optional[str] = None) -> list[Agendamento]:
        """Próximas N horas, pendentes, sem confirmação enviada."""
        agora = agora_utc()
        janela = agora + timedelta(hours=settings.INTERVALO_CONFIRMACAO_HORAS)
        q = (
            self.db.query(Agendamento)
            .join(Clinica, Agendamento.clinica_id == Clinica.id)
            .filter(
                Clinica.ativo == True,
                Clinica.evolution_conectado == True,
                Agendamento.status == Status.PENDENTE,
                Agendamento.confirmacao_enviada == False,
                Agendamento.data_hora >= agora,
                Agendamento.data_hora <= janela,
            )
        )
        if clinica_id:
            q = q.filter(Agendamento.clinica_id == clinica_id)
        return q.all()

    def buscar_agendamentos_pra_lembrete(self, clinica_id: Optional[str] = None) -> list[Agendamento]:
        """Próximas 2h, pendentes, com confirmação enviada mas sem resposta."""
        agora = agora_utc()
        janela = agora + timedelta(hours=settings.INTERVALO_LEMBRETE_HORAS)
        q = (
            self.db.query(Agendamento)
            .join(Clinica, Agendamento.clinica_id == Clinica.id)
            .filter(
                Clinica.ativo == True,
                Clinica.evolution_conectado == True,
                Agendamento.status == Status.PENDENTE,
                Agendamento.confirmacao_enviada == True,
                Agendamento.segunda_confirmacao == False,
                Agendamento.data_hora >= agora,
                Agendamento.data_hora <= janela,
            )
        )
        if clinica_id:
            q = q.filter(Agendamento.clinica_id == clinica_id)
        return q.all()

    # ========================================================================
    # ENVIOS PROATIVOS (cron)
    # ========================================================================

    def enviar_confirmacao(self, agendamento: Agendamento) -> bool:
        return self._enviar(agendamento, template_key="msg_confirmacao_24h",
                            flag_attr="confirmacao_enviada", tipo=TipoInteracao.CONFIRMACAO)

    def enviar_lembrete(self, agendamento: Agendamento) -> bool:
        return self._enviar(agendamento, template_key="msg_lembrete_2h",
                            flag_attr="segunda_confirmacao", tipo=TipoInteracao.LEMBRETE)

    def _enviar(self, agendamento: Agendamento, *, template_key: str, flag_attr: str, tipo: str) -> bool:
        """R2: DRY de enviar_confirmacao/enviar_lembrete."""
        clinica = agendamento.clinica
        paciente = agendamento.paciente
        if paciente.opt_out or paciente.deletado_em is not None:
            return False
        template = self._template(clinica.id, template_key)
        if not template:
            return False
        mensagem = self._render(template, paciente, clinica, agendamento.data_hora)
        result = self.whatsapp.enviar_mensagem(
            instance_name=clinica.evolution_instance_name,
            telefone=paciente.telefone,
            mensagem=mensagem,
        )
        if not result.get("success"):
            return False
        setattr(agendamento, flag_attr, True)
        self.db.add(Interacao(
            clinica_id=clinica.id,
            agendamento_id=agendamento.id,
            tipo=tipo,
            mensagem_enviada=mensagem,
        ))
        self.db.commit()
        return True

    # ========================================================================
    # PROCESSA RESPOSTA DO PACIENTE
    # ========================================================================

    def processar_resposta_paciente(
        self,
        clinica: Clinica,
        telefone: str,
        mensagem: str,
        evolution_message_id: Optional[str] = None,
    ) -> dict:
        """Chamado pelo webhook quando paciente responde no WhatsApp.

        Fluxo:
        1. Identifica paciente
        2. Opt-out (LGPD Art. 8 §5) — sempre tem prioridade
        3. Se houver EstadoConversa ATIVO → processa como continuação (G1)
        4. Senão → classifica intenção e despacha
        """
        paciente = (
            self.db.query(Paciente)
            .filter(
                Paciente.telefone == telefone,
                Paciente.clinica_id == clinica.id,
                Paciente.deletado_em.is_(None),
            )
            .first()
        )

        # Opt-out tem precedência sobre tudo
        intencao = self.processor.classificar_resposta(mensagem)
        if intencao == INTENCAO_OPT_OUT and paciente:
            return self._processar_opt_out(clinica, paciente, mensagem, evolution_message_id)

        if not paciente:
            self._safe_add_interacao(
                clinica_id=clinica.id, agendamento_id=None,
                tipo=TipoInteracao.RESPOSTA,
                mensagem_recebida=mensagem,
                evolution_message_id=evolution_message_id,
            )
            self.db.commit()
            return {"status": "ignored", "reason": "no_patient"}

        # G1: estado de conversa ativo? (fluxo multi-step)
        estado = self._estado_ativo(clinica.id, paciente.id)
        if estado and estado.fluxo == FluxoConversa.REAGENDAMENTO:
            return self._processar_escolha_reagendamento(
                clinica, paciente, estado, mensagem, evolution_message_id
            )

        # Fluxo single-shot: pega agendamento pendente + classifica
        agendamento_q = (
            self.db.query(Agendamento)
            .filter(
                Agendamento.paciente_id == paciente.id,
                Agendamento.clinica_id == clinica.id,
                Agendamento.status == Status.PENDENTE,
                Agendamento.data_hora > agora_utc(),
            )
            .order_by(Agendamento.data_hora.asc())
        )
        try:
            agendamento = agendamento_q.with_for_update(skip_locked=True).first()
        except Exception:
            agendamento = agendamento_q.first()

        if not agendamento:
            self._safe_add_interacao(
                clinica_id=clinica.id, agendamento_id=None,
                tipo=TipoInteracao.RESPOSTA,
                mensagem_recebida=mensagem,
                evolution_message_id=evolution_message_id,
            )
            self.db.commit()
            return {"status": "ignored", "reason": "no_pending_appointment"}

        dedup_ok = self._safe_add_interacao(
            clinica_id=clinica.id, agendamento_id=agendamento.id,
            tipo=TipoInteracao.RESPOSTA,
            mensagem_recebida=mensagem,
            evolution_message_id=evolution_message_id,
        )
        if not dedup_ok:
            return {"status": "dedup"}

        # Despacha por intenção
        if intencao == Status.CONFIRMADO:
            agendamento.status = Status.CONFIRMADO
            self._enviar_template_resposta(clinica, paciente, "msg_confirmado", agendamento)
        elif intencao == Status.CANCELADO:
            agendamento.status = Status.CANCELADO
            self._enviar_template_resposta(clinica, paciente, "msg_cancelado", agendamento)
        elif intencao == INTENCAO_REAGENDAR:
            self._iniciar_fluxo_reagendamento(clinica, paciente, agendamento)
        else:  # nao_entendido
            self._enviar_template_resposta(clinica, paciente, "msg_nao_entendido", agendamento)

        self.db.commit()
        return {
            "status": "processed",
            "intencao": intencao,
            "agendamento_id": agendamento.id,
            "novo_status": agendamento.status,
        }

    # ========================================================================
    # G1: FLUXO DE REAGENDAMENTO MULTI-STEP
    # ========================================================================

    def _iniciar_fluxo_reagendamento(self, clinica: Clinica, paciente: Paciente, agendamento: Agendamento) -> None:
        """Calcula slots livres e abre estado de conversa."""
        slots = sugerir_slots(
            self.db, clinica,
            n_slots=3, dias_a_frente=7,
            excluir_agendamento_id=agendamento.id,
        )

        if not slots:
            self._enviar_msg(
                clinica, paciente.telefone,
                f"Oi {paciente.nome}! Infelizmente não tenho horários livres nos próximos 7 dias. "
                "Vamos achar um juntas? Me responde com 1-2 horários que funcionam pra você."
            )
            agendamento.status = Status.REAGENDADO
            return

        # Cria estado de conversa (substitui se já existir)
        existente = self._estado_ativo(clinica.id, paciente.id)
        if existente:
            self.db.delete(existente)
            self.db.flush()

        estado = EstadoConversa(
            clinica_id=clinica.id,
            paciente_id=paciente.id,
            fluxo=FluxoConversa.REAGENDAMENTO,
            contexto={
                "agendamento_id": agendamento.id,
                "slots_oferecidos": [
                    {
                        "numero": s["numero"],
                        "data_hora_utc": s["data_hora_utc"].isoformat(),
                        "label": s["label"],
                    }
                    for s in slots
                ],
            },
            expira_em=agora_utc() + timedelta(hours=24),
        )
        self.db.add(estado)

        # Envia mensagem com opções.
        # Cadeia de fallback: Configuracao do tenant -> template da vertical -> genérico.
        template = self._template(clinica.id, "msg_reagendar_opcoes")
        if not template:
            try:
                from core.especialidades import get_especialidade
                template = get_especialidade(clinica.especialidade).mensagens_whatsapp.get("reagendamento")
            except Exception:  # noqa: BLE001 — defensivo, nunca quebra fluxo
                template = None
        opcoes_txt = formatar_opcoes_pra_mensagem(slots)
        msg = self._render(
            template, paciente, clinica, agendamento.data_hora,
            opcoes=opcoes_txt,
        ) if template else (
            f"Sem problemas, {paciente.nome}! Tenho estes horários disponíveis:\n\n{opcoes_txt}\n\n"
            "Responde com o número da opção."
        )
        self._enviar_msg(clinica, paciente.telefone, msg, agendamento_id=agendamento.id,
                         tipo=TipoInteracao.REAGENDAMENTO)
        # Status NÃO muda ainda — só quando paciente escolher

    def _processar_escolha_reagendamento(
        self,
        clinica: Clinica,
        paciente: Paciente,
        estado: EstadoConversa,
        mensagem: str,
        evolution_message_id: Optional[str],
    ) -> dict:
        slots = estado.contexto.get("slots_oferecidos", [])
        agendamento_id = estado.contexto.get("agendamento_id")

        # Log da resposta
        dedup_ok = self._safe_add_interacao(
            clinica_id=clinica.id, agendamento_id=agendamento_id,
            tipo=TipoInteracao.REAGENDAMENTO,
            mensagem_recebida=mensagem,
            evolution_message_id=evolution_message_id,
        )
        if not dedup_ok:
            return {"status": "dedup"}

        # Tenta parsear número
        n = extrair_numero_resposta(mensagem, max_num=len(slots))
        if n is None:
            opcoes_txt = "\n".join(f"{s['numero']}. {s['label']}" for s in slots)
            self._enviar_msg(
                clinica, paciente.telefone,
                f"Desculpa {paciente.nome}, não entendi qual horário você quer. "
                f"Me responde com o número:\n\n{opcoes_txt}",
                agendamento_id=agendamento_id, tipo=TipoInteracao.REAGENDAMENTO,
            )
            self.db.commit()
            return {"status": "aguardando_resposta_valida"}

        escolhido = next((s for s in slots if s["numero"] == n), None)
        if not escolhido:
            self._enviar_msg(
                clinica, paciente.telefone,
                f"Esse número não está nas opções. Escolhe entre 1 e {len(slots)}, por favor.",
                agendamento_id=agendamento_id, tipo=TipoInteracao.REAGENDAMENTO,
            )
            self.db.commit()
            return {"status": "numero_invalido"}

        # Aplica reagendamento
        agendamento = self.db.query(Agendamento).filter(
            Agendamento.id == agendamento_id,
            Agendamento.clinica_id == clinica.id,
        ).first()
        if not agendamento:
            self.db.delete(estado)
            self.db.commit()
            return {"status": "agendamento_removido"}

        nova_data_utc = datetime.fromisoformat(escolhido["data_hora_utc"])
        agendamento.data_hora = nova_data_utc
        agendamento.status = Status.PENDENTE
        agendamento.confirmacao_enviada = False
        agendamento.segunda_confirmacao = False

        # Limpa estado
        self.db.delete(estado)

        # Confirma pra paciente
        nova_data_br = from_utc_to_br(nova_data_utc).strftime("%d/%m às %H:%M")
        msg = (
            f"Pronto, {paciente.nome}! Reagendei sua consulta pra {nova_data_br}. "
            "Vou te lembrar 24h antes 💛"
        )
        self._enviar_msg(clinica, paciente.telefone, msg,
                         agendamento_id=agendamento.id, tipo=TipoInteracao.REAGENDAMENTO)

        self.db.commit()
        log.info("reagendamento_concluido: cli=%s pac=%s ag=%s -> %s",
                 clinica.id, paciente.id, agendamento.id, nova_data_utc.isoformat())
        return {
            "status": "reagendado_confirmado",
            "agendamento_id": agendamento.id,
            "novo_horario_utc": nova_data_utc.isoformat(),
        }

    # ========================================================================
    # OPT-OUT (LGPD)
    # ========================================================================

    def _processar_opt_out(self, clinica: Clinica, paciente: Paciente,
                           mensagem: str, evolution_message_id: Optional[str]) -> dict:
        paciente.opt_out = True
        paciente.opt_out_em = agora_utc()
        self._safe_add_interacao(
            clinica_id=clinica.id, agendamento_id=None,
            tipo=TipoInteracao.OPT_OUT,
            mensagem_recebida=mensagem,
            evolution_message_id=evolution_message_id,
        )
        # Limpa estado de conversa também (se tiver)
        estado = self._estado_ativo(clinica.id, paciente.id)
        if estado:
            self.db.delete(estado)

        self.whatsapp.enviar_mensagem(
            instance_name=clinica.evolution_instance_name,
            telefone=paciente.telefone,
            mensagem="Tudo bem! Não vou mais enviar mensagens automáticas. "
                     "Quando quiser, basta nos chamar diretamente. 💛",
        )
        self.db.commit()
        return {"status": "opt_out"}

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _estado_ativo(self, clinica_id: str, paciente_id: str) -> Optional[EstadoConversa]:
        return (
            self.db.query(EstadoConversa)
            .filter(
                EstadoConversa.clinica_id == clinica_id,
                EstadoConversa.paciente_id == paciente_id,
                EstadoConversa.expira_em > agora_utc(),
            )
            .first()
        )

    def _enviar_template_resposta(self, clinica: Clinica, paciente: Paciente,
                                   template_key: str, agendamento: Agendamento) -> None:
        template = self._template(clinica.id, template_key)
        if not template:
            return
        msg = self._render(template, paciente, clinica, agendamento.data_hora)
        self._enviar_msg(clinica, paciente.telefone, msg, agendamento_id=agendamento.id,
                         tipo=TipoInteracao.RESPOSTA)

    def _enviar_msg(
        self, clinica: Clinica, telefone: str, mensagem: str,
        *, agendamento_id: Optional[str] = None, tipo: str = TipoInteracao.RESPOSTA,
    ) -> bool:
        result = self.whatsapp.enviar_mensagem(
            instance_name=clinica.evolution_instance_name,
            telefone=telefone,
            mensagem=mensagem,
        )
        if not result.get("success"):
            log.warning("falha envio whatsapp: cli=%s tel=%s err=%s",
                        clinica.id, telefone, result.get("error"))
            return False
        self.db.add(Interacao(
            clinica_id=clinica.id,
            agendamento_id=agendamento_id,
            tipo=tipo,
            mensagem_enviada=mensagem,
        ))
        return True

    def _safe_add_interacao(self, **kwargs) -> bool:
        """G9: tenta inserir, captura UNIQUE violation em evolution_message_id."""
        try:
            self.db.add(Interacao(**kwargs))
            self.db.flush()
            return True
        except IntegrityError:
            self.db.rollback()
            return False

    def _template(self, clinica_id: str, chave: str) -> Optional[str]:
        config = (
            self.db.query(Configuracao)
            .filter(Configuracao.clinica_id == clinica_id, Configuracao.chave == chave)
            .first()
        )
        return config.valor if config else None

    def _render(
        self, template: str, paciente: Paciente, clinica: Clinica,
        data_hora: datetime, **extras,
    ) -> str:
        """F18: usa string.Template.safe_substitute em vez de str.format pra evitar SSTI."""
        from string import Template as StrTemplate
        try:
            data_hora_str = str(data_hora)
            if isinstance(data_hora, datetime):
                data_hora_str = from_utc_to_br(data_hora).strftime("%d/%m às %H:%M")
            template_safe = (
                template
                .replace("{nome}", "$nome")
                .replace("{clinica}", "$clinica")
                .replace("{data_hora}", "$data_hora")
                .replace("{opcoes}", "$opcoes")
                .replace("{sessoes_restantes}", "$sessoes_restantes")
            )
            return StrTemplate(template_safe).safe_substitute(
                nome=paciente.nome,
                clinica=clinica.nome,
                data_hora=data_hora_str,
                **extras,
            )
        except (KeyError, IndexError, ValueError):
            return template
