"""Senha padrão Access no cofre de credenciais do usuário Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


CREDENTIAL_TARGET = "Circuit Viewer/Access/default-password"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _credential_api():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_Credential)))
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = (ctypes.POINTER(_Credential), wintypes.DWORD)
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = (ctypes.c_void_p,)
    api.CredFree.restype = None
    return api


def load_default_password() -> str | None:
    """Cofre indisponível ou sem entrada mantém a solicitação manual normal."""
    if os.name != "nt":
        return None
    try:
        api = _credential_api()
        credential = ctypes.POINTER(_Credential)()
        if not api.CredReadW(CREDENTIAL_TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
            return None
        try:
            value = credential.contents
            return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-16-le") or None
        finally:
            api.CredFree(credential)
    except (OSError, UnicodeError, ValueError):
        return None


def save_default_password(password: str) -> None:
    """Configura a credencial local; nunca grava senha no repositório ou em logs."""
    if os.name != "nt":
        raise OSError("O armazenamento de senha padrão requer Windows.")
    if not password:
        raise ValueError("Informe uma senha padrão não vazia.")
    encoded = password.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _Credential()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = CREDENTIAL_TARGET
    credential.Comment = "Senha padrão para abertura de bancos Access no Circuit Viewer"
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = blob
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    try:
        if not _credential_api().CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "Não foi possível salvar a senha padrão no cofre do Windows.")
    finally:
        ctypes.memset(blob, 0, len(encoded))
