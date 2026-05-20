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
    IAProcessor, INTENCAO_AGENDAR, INTENCAO_REAGENDAR, INTENCAO_NAO_ENTENDIDO,
    INTENCAO_OPT_OUT,
)
from services.slots import (
    extrair_numero_resposta, formatar_opcoes_pra_mensagem, sugerir_slots,
)
from services.whatsapp import WhatsAppService

log = logging.getLogger("recepia.scheduler")

# Fallback de mensagens quando a clínica não cadastrou o template em Configuracao.
# Sem isso o bot ficava MUDO ao não entender — pior experiência possível.
_DEFAULT_RESPOSTAS = {
    "msg_confirmado": "Perfeito, {nome}! ✅ Sua consulta de {data_hora} está confirmada. Te espero! 💛",
    "msg_cancelado": "Tudo bem, {nome}. Sua consulta de {data_hora} foi cancelada. Quando quiser remarcar, é só me chamar 💛",
    "msg_nao_entendido": ("Desculpa, {nome}, não entendi bem 😅 Posso te ajudar a "
                          "confirmar, remarcar ou cancelar sua consulta — é só me dizer o que precisa."),
}


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
        push_name: Optional[str] = None,
    ) -> dict:
        """Chamado pelo webhook quando paciente responde no WhatsApp.

        Fluxo:
        1. Identifica paciente (pode não existir — contato novo)
        2. Opt-out (LGPD Art. 8 §5) — sempre tem prioridade
        3. Se houver EstadoConversa ATIVO → processa como continuação (G1)
        4. Contato novo → agenda (se pediu) ou cumprimenta
        5. Paciente sem agendamento pendente → agenda novo / recusa / cumprimenta
        6. Paciente com agendamento pendente → confirma / cancela / reagenda
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

        # Opt-out tem precedência sobre tudo (precisa de paciente pra registrar)
        intencao = self.processor.classificar_resposta(mensagem)
        if intencao == INTENCAO_OPT_OUT and paciente:
            return self._processar_opt_out(clinica, paciente, mensagem, evolution_message_id)

        # G1: estado de conversa ativo? (fluxo multi-step) — só pra paciente conhecido
        if paciente:
            estado = self._estado_ativo(clinica.id, paciente.id)
            if estado and estado.fluxo == FluxoConversa.REAGENDAMENTO:
                return self._processar_escolha_reagendamento(
                    clinica, paciente, estado, mensagem, evolution_message_id
                )
            if estado and estado.fluxo == FluxoConversa.NOVO_AGENDAMENTO:
                return self._processar_escolha_agendamento(
                    clinica, paciente, estado, mensagem, evolution_message_id
                )

        # ── Contato novo (número não cadastrado) ───────────────────────────
        if not paciente:
            log_ok = self._safe_add_interacao(
                clinica_id=clinica.id, agendamento_id=None,
                tipo=TipoInteracao.RESPOSTA,
                mensagem_recebida=mensagem,
                evolution_message_id=evolution_message_id,
            )
            if not log_ok:
                return {"status": "dedup"}
            if intencao == INTENCAO_AGENDAR:
                # Gap 1: marca consulta nova — cria o paciente dentro do fluxo
                return self._iniciar_fluxo_agendamento(
                    clinica, None, telefone, push_name, mensagem
                )
            # Gap 3: número novo sem intenção clara → boas-vindas (convida a marcar)
            self._enviar_msg(clinica, telefone, self._boas_vindas_msg(clinica),
                             tipo=TipoInteracao.RESPOSTA)
            self.db.commit()
            return {"status": "boas_vindas"}

        # ── Paciente conhecido: busca agendamento pendente futuro ──────────
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

        # ── Sem agendamento pendente → marca novo / recusa / cumprimenta ───
        if not agendamento:
            log_ok = self._safe_add_interacao(
                clinica_id=clinica.id, agendamento_id=None,
                tipo=TipoInteracao.RESPOSTA,
                mensagem_recebida=mensagem,
                evolution_message_id=evolution_message_id,
            )
            if not log_ok:
                return {"status": "dedup"}
            # Gap 1 + Gap 2: "sim"/"quero marcar" — inclui resposta afirmativa a um recall
            if intencao in (INTENCAO_AGENDAR, Status.CONFIRMADO, INTENCAO_REAGENDAR):
                return self._iniciar_fluxo_agendamento(
                    clinica, paciente, telefone, push_name, mensagem
                )
            if intencao == Status.CANCELADO:
                self._enviar_msg(
                    clinica, telefone,
                    f"Tudo bem, {paciente.nome}! Quando quiser marcar, é só me chamar 💛",
                    tipo=TipoInteracao.RESPOSTA,
                )
                self.db.commit()
                return {"status": "recusou"}
            self._enviar_msg(clinica, telefone, self._boas_vindas_msg(clinica),
                             tipo=TipoInteracao.RESPOSTA)
            self.db.commit()
            return {"status": "boas_vindas"}

        # ── Paciente com agendamento pendente → confirma/cancela/reagenda ──
        dedup_ok = self._safe_add_interacao(
            clinica_id=clinica.id, agendamento_id=agendamento.id,
            tipo=TipoInteracao.RESPOSTA,
            mensagem_recebida=mensagem,
            evolution_message_id=evolution_message_id,
        )
        if not dedup_ok:
            return {"status": "dedup"}

        if intencao == Status.CONFIRMADO:
            agendamento.status = Status.CONFIRMADO
            self._enviar_template_resposta(clinica, paciente, "msg_confirmado", agendamento)
        elif intencao == Status.CANCELADO:
            agendamento.status = Status.CANCELADO
            self._enviar_template_resposta(clinica, paciente, "msg_cancelado", agendamento)
        elif intencao == INTENCAO_REAGENDAR:
            self._iniciar_fluxo_reagendamento(clinica, paciente, agendamento)
        elif intencao == INTENCAO_AGENDAR:
            # paciente quer marcar OUTRA consulta além da pendente
            return self._iniciar_fluxo_agendamento(
                clinica, paciente, telefone, push_name, mensagem
            )
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
    # GAP 1: FLUXO DE AGENDAMENTO NOVO (multi-step)
    # ========================================================================

    def _iniciar_fluxo_agendamento(
        self,
        clinica: Clinica,
        paciente: Optional[Paciente],
        telefone: str,
        push_name: Optional[str],
        servico_hint: str,
    ) -> dict:
        """Oferece horários livres pra marcar uma consulta nova.

        `paciente` pode ser None (contato novo) — nesse caso cria o registro,
        porque EstadoConversa exige paciente_id. A mensagem de entrada já deve
        ter sido logada pelo chamador (dedup feito lá).
        """
        slots = sugerir_slots(self.db, clinica, n_slots=3, dias_a_frente=7)

        if not paciente:
            nome = (push_name or "").strip() or "Novo contato"
            paciente = Paciente(clinica_id=clinica.id, nome=nome[:120], telefone=telefone)
            self.db.add(paciente)
            self.db.flush()

        if not slots:
            self._enviar_msg(
                clinica, telefone,
                f"Oi {paciente.nome}! No momento não tenho horários livres pra te oferecer. "
                "Me diz 1 ou 2 horários que funcionam pra você que a recepção te encaixa 💛",
                tipo=TipoInteracao.AGENDAMENTO,
            )
            self.db.commit()
            return {"status": "agendamento_sem_slots", "paciente_id": paciente.id}

        # 1 estado ativo por paciente — substitui se já existir
        existente = self._estado_ativo(clinica.id, paciente.id)
        if existente:
            self.db.delete(existente)
            self.db.flush()

        estado = EstadoConversa(
            clinica_id=clinica.id,
            paciente_id=paciente.id,
            fluxo=FluxoConversa.NOVO_AGENDAMENTO,
            contexto={
                "slots_oferecidos": [
                    {
                        "numero": s["numero"],
                        "data_hora_utc": s["data_hora_utc"].isoformat(),
                        "label": s["label"],
                    }
                    for s in slots
                ],
                "servico_hint": (servico_hint or "").strip()[:200],
            },
            expira_em=agora_utc() + timedelta(hours=24),
        )
        self.db.add(estado)

        opcoes_txt = formatar_opcoes_pra_mensagem(slots)
        msg = (
            f"Oi {paciente.nome}! 😊 Vou te ajudar a marcar. "
            f"Tenho estes horários disponíveis:\n\n{opcoes_txt}\n\n"
            "Responde com o número da opção que preferir."
        )
        self._enviar_msg(clinica, telefone, msg, tipo=TipoInteracao.AGENDAMENTO)
        self.db.commit()
        return {"status": "agendamento_iniciado", "paciente_id": paciente.id}

    def _processar_escolha_agendamento(
        self,
        clinica: Clinica,
        paciente: Paciente,
        estado: EstadoConversa,
        mensagem: str,
        evolution_message_id: Optional[str],
    ) -> dict:
        slots = estado.contexto.get("slots_oferecidos", [])
        servico_hint = estado.contexto.get("servico_hint", "")

        dedup_ok = self._safe_add_interacao(
            clinica_id=clinica.id, agendamento_id=None,
            tipo=TipoInteracao.AGENDAMENTO,
            mensagem_recebida=mensagem,
            evolution_message_id=evolution_message_id,
        )
        if not dedup_ok:
            return {"status": "dedup"}

        n = extrair_numero_resposta(mensagem, max_num=len(slots))
        if n is None:
            # Sem número: pode ser desistência. (Opt-out já foi tratado no topo.)
            intencao = self.processor.classificar_resposta(mensagem)
            if intencao == Status.CANCELADO:
                self.db.delete(estado)
                self._enviar_msg(
                    clinica, paciente.telefone,
                    f"Sem problema, {paciente.nome}! Quando quiser marcar, é só me chamar 💛",
                    tipo=TipoInteracao.AGENDAMENTO,
                )
                self.db.commit()
                return {"status": "agendamento_desistiu"}
            opcoes_txt = "\n".join(f"{s['numero']}. {s['label']}" for s in slots)
            self._enviar_msg(
                clinica, paciente.telefone,
                f"Desculpa {paciente.nome}, não entendi qual horário você quer. "
                f"Me responde só com o número:\n\n{opcoes_txt}",
                tipo=TipoInteracao.AGENDAMENTO,
            )
            self.db.commit()
            return {"status": "aguardando_resposta_valida"}

        escolhido = next((s for s in slots if s["numero"] == n), None)
        if not escolhido:
            self._enviar_msg(
                clinica, paciente.telefone,
                f"Esse número não está nas opções. Escolhe entre 1 e {len(slots)}, por favor.",
                tipo=TipoInteracao.AGENDAMENTO,
            )
            self.db.commit()
            return {"status": "numero_invalido"}

        data_utc = datetime.fromisoformat(escolhido["data_hora_utc"])

        # Corrida: outro paciente pode ter pego esse horário desde a oferta
        ja_ocupado = (
            self.db.query(Agendamento)
            .filter(
                Agendamento.clinica_id == clinica.id,
                Agendamento.data_hora == data_utc,
                Agendamento.status.in_([Status.PENDENTE, Status.CONFIRMADO]),
            )
            .first()
        )
        if ja_ocupado:
            # reabre o fluxo com horários atualizados (substitui o estado atual)
            return self._iniciar_fluxo_agendamento(
                clinica, paciente, paciente.telefone, None, servico_hint
            )

        servico = (
            f"WhatsApp: {servico_hint}"[:120] if servico_hint else "Agendado pelo WhatsApp"
        )
        agendamento = Agendamento(
            clinica_id=clinica.id,
            paciente_id=paciente.id,
            data_hora=data_utc,
            status=Status.PENDENTE,
            servico=servico,
        )
        self.db.add(agendamento)
        self.db.flush()
        self.db.delete(estado)

        data_br = from_utc_to_br(data_utc).strftime("%d/%m às %H:%M")
        self._enviar_msg(
            clinica, paciente.telefone,
            f"Prontinho, {paciente.nome}! ✅ Sua consulta ficou marcada pra {data_br}. "
            "Vou te lembrar 24h antes. Até lá! 💛",
            agendamento_id=agendamento.id, tipo=TipoInteracao.AGENDAMENTO,
        )
        self.db.commit()
        log.info("agendamento_via_whatsapp: cli=%s pac=%s ag=%s -> %s",
                 clinica.id, paciente.id, agendamento.id, data_utc.isoformat())
        return {
            "status": "agendamento_confirmado",
            "agendamento_id": agendamento.id,
            "horario_utc": data_utc.isoformat(),
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
        # Template do tenant; se não cadastrado, cai no default — nunca fica mudo.
        template = self._template(clinica.id, template_key) or _DEFAULT_RESPOSTAS.get(template_key)
        if not template:
            return
        msg = self._render(template, paciente, clinica, agendamento.data_hora)
        self._enviar_msg(clinica, paciente.telefone, msg, agendamento_id=agendamento.id,
                         tipo=TipoInteracao.RESPOSTA)

    def _boas_vindas_msg(self, clinica: Clinica) -> str:
        """Gap 3: saudação pra número novo / mensagem sem contexto. Usa o template
        da vertical da clínica (`boas_vindas`); cai pra genérico se não houver."""
        tpl = None
        try:
            from core.especialidades import get_especialidade
            tpl = get_especialidade(clinica.especialidade).mensagens_whatsapp.get("boas_vindas")
        except Exception:  # noqa: BLE001 — defensivo, nunca quebra o fluxo
            tpl = None
        if not tpl:
            tpl = ("Olá! 😊 Aqui é o atendimento da {clinica}. "
                   "Posso te ajudar a marcar um horário — é só me dizer o que você precisa!")
        return tpl.replace("{clinica}", clinica.nome)

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
