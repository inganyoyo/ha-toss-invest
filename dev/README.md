# Home Assistant development environment

This Compose environment is isolated from production Home Assistant. Start and
stop it from the repository root:

```console
docker compose -f dev/compose.yaml up -d
docker compose -f dev/compose.yaml down --remove-orphans
```

The checked-in `dev/secrets.yaml.example` is mounted by default, so startup needs
no local credentials. To use a git-ignored `dev/secrets.yaml`, copy the example,
fill it locally, and run this exact command from the repository root:

```console
TOSS_INVEST_DEV_SECRETS=./secrets.yaml docker compose -f dev/compose.yaml up -d
```

Only Home Assistant registry and authentication state stored under
`/config/.storage` persists on the host in `dev/config/.storage`. The recorder
database, history, logs, and other generated files are intentionally disposable.
The development configuration, example secrets, and integration source are
mounted separately so Docker Desktop never receives overlapping mount targets.
