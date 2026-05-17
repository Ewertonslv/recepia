"""Rate limiter compartilhado (F7).

In-memory por padrão (limita por instância). Em produção multi-instância,
configurar `storage_uri="redis://..."` pra evitar bypass.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
