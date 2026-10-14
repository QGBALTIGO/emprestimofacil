"""Import provider settings from a private file; never print credential values."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

ALLOWED = frozenset({
    'EMAIL_PROVIDER', 'RESEND_API_KEY', 'EMAIL_FROM', 'TWILIO_ACCOUNT_SID',
    'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID', 'BELVO_ENVIRONMENT',
    'BELVO_SECRET_ID', 'BELVO_SECRET_PASSWORD', 'BELVO_WEBHOOK_TOKEN',
    'COMPANY_NAME', 'COMPANY_TERMS_URL', 'CAKTO_CLIENT_ID', 'CAKTO_CLIENT_SECRET',
    'CAKTO_WEBHOOK_SECRET',
})


class ConfigurationError(Exception):
    pass


def parse(text, *, import_only=False):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '=' not in line:
            raise ConfigurationError('Use uma chave por linha no formato CHAVE=valor.')
        key, value = line.split('=', 1)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in result:
            raise ConfigurationError('O arquivo contém uma chave inválida ou repetida.')
        if import_only and key not in ALLOWED:
            raise ConfigurationError('O arquivo contém configuração fora das integrações permitidas.')
        if len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ConfigurationError('O arquivo contém um valor inválido.')
        result[key] = value
    return result


def validate(settings, changed):
    if not changed:
        raise ConfigurationError('Nenhuma configuração de integração foi informada.')
    if settings.get('EMAIL_PROVIDER', 'twilio') not in {'resend', 'twilio'}:
        raise ConfigurationError('EMAIL_PROVIDER deve ser resend ou twilio.')
    if settings.get('BELVO_ENVIRONMENT', 'sandbox') not in {'sandbox', 'production'}:
        raise ConfigurationError('BELVO_ENVIRONMENT deve ser sandbox ou production.')
    for key, prefix in [('TWILIO_ACCOUNT_SID', 'AC'), ('TWILIO_VERIFY_SERVICE_SID', 'VA')]:
        value = settings.get(key, '')
        if value and not re.fullmatch(prefix + r'[0-9a-fA-F]{32}', value):
            raise ConfigurationError(key + ' está em formato inválido.')
    sender = settings.get('EMAIL_FROM', '')
    if sender:
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', sender):
            raise ConfigurationError('EMAIL_FROM precisa ser um endereço do domínio verificado.')
        domain = sender.split('@')[1].lower()
        origin_domain = urlsplit(settings.get('PUBLIC_ORIGIN', '')).hostname
        if not origin_domain or not (domain == origin_domain or domain.endswith('.' + origin_domain)):
            raise ConfigurationError('EMAIL_FROM precisa pertencer ao domínio público da operação.')
    terms = settings.get('COMPANY_TERMS_URL', '')
    if terms:
        parsed = urlsplit(terms)
        origin = urlsplit(settings.get('PUBLIC_ORIGIN', ''))
        if (parsed.scheme != 'https' or parsed.netloc != origin.netloc or
                parsed.username or parsed.password or not parsed.path or parsed.fragment):
            raise ConfigurationError('COMPANY_TERMS_URL precisa ser uma página HTTPS do domínio público.')
    for key in ['BELVO_WEBHOOK_TOKEN', 'CAKTO_WEBHOOK_SECRET']:
        if settings.get(key) and len(settings[key]) < 32:
            raise ConfigurationError(key + ' precisa ter pelo menos 32 caracteres.')


def private_read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ConfigurationError('Use um arquivo privado regular, sem link simbólico.')
    if os.name == 'posix':
        info = path.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConfigurationError('O arquivo deve pertencer ao usuário atual e ter permissão 0600.')
    return path.read_text(encoding='utf-8')


def atomic_private_write(path, text):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def import_settings(target, source, *, apply=False):
    target, source = Path(target), Path(source)
    original = private_read(target)
    settings = parse(original)
    changed = parse(private_read(source), import_only=True)
    combined = {**settings, **changed}
    validate(combined, changed)
    if apply:
        if os.name != 'posix':
            raise ConfigurationError('A gravação deve ser feita na VPS Linux para garantir permissões privadas.')
        # Keep all non-provider settings and their values, including encryption and activation flags.
        lines = []
        remaining = dict(changed)
        for line in original.splitlines():
            key = line.split('=', 1)[0] if '=' in line and not line.lstrip().startswith('#') else None
            lines.append(key + '=' + remaining.pop(key) if key in remaining else line)
        lines.extend(key + '=' + value for key, value in remaining.items())
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = target.with_name(target.name + '.backup-' + timestamp)
        # Exclusive creation prevents replacing an older backup.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(original)
            file.flush()
            os.fsync(file.fileno())
        atomic_private_write(target, '\n'.join(lines) + '\n')
    return {'applied': apply, 'updated_keys': sorted(changed),
            'configured': {key: bool(combined.get(key)) for key in sorted(ALLOWED)},
            'service_restart_required': apply,
            'note': 'Configuração validada; entrega, domínio verificado e homologação ainda precisam de teste real.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=Path('/etc/emprestimofacil/backend.env'))
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = import_settings(args.target, args.input, apply=args.apply)
    except (ConfigurationError, OSError, UnicodeError):
        parser.exit(1, 'Não foi possível importar: verifique formato, domínio e permissões dos arquivos privados. Nenhuma credencial foi exibida.\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
