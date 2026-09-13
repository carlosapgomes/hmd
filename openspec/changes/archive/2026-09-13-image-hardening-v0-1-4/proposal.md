# Change: image-hardening-v0-1-4

## Why

Riscos residuais aceitos na fase 1 do piloto, agora cobrados para a próxima
release: (1) a imagem roda o gunicorn como **root** (mitigado por
read_only/no-new-privileges/cap_drop ALL, mas a classe de risco persiste —
anotado no próprio compose como "non-root fica p/ o próximo release");
(2) `config/wsgi.py` defaulta para settings de **dev** — armadilha: subir a
imagem sem `DJANGO_SETTINGS_MODULE` levanta o site com configuração de
desenvolvimento (o piloto não sofre porque o compose seta a env; o fix do P1
em `b07e62d` foi sintomático).

## What Changes

- `Dockerfile`: usuário dedicado não-root (uid/gid fixos 10001), `/app/media`
  criado na imagem com ownership do usuário (volumes nomeados herdam o
  ownership do mountpoint na primeira montagem — fase 2 com uploads já
  nasce certo), `USER 10001:10001` antes do CMD; estáticos/venv seguem
  legíveis (umask padrão), nada precisa de root.
- `config/wsgi.py`: default `config.settings.prod` (env continua vencendo;
  esquecer a env agora cai no MAIS seguro, não no menos).
- Release **v0.1.4**: CHANGELOG, bump pyproject/uv.lock, default
  `HMD_IMAGE_TAG` no compose e pins do README. Tag/imagem GHCR só sob
  autorização do dono, após o gate (como em v0.1.3).
- Spec `production-deployment` MODIFIED ×2 (runtime com usuário não-root;
  fail-closed inclui o default do WSGI) com cenários novos.

## Impact

- **Specs**: `production-deployment` (2 MODIFIED, +2 cenários).
- **Código**: `Dockerfile`, `config/wsgi.py`, `docker-compose.prod.yml`
  (comentário do risco + default da tag), `pyproject.toml`/`uv.lock`,
  `CHANGELOG.md`, `README.md`, testes.
- **Risco**: baixo — usuário não-root não escreve em FS na fase 1 (intake
  off; /tmp é tmpfs 1777; cache é banco). Validação de imagem (build + boot
  como não-root + healthz) é executada pelo parent, como no piloto.
