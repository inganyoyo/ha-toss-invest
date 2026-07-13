# Home Assistant development environment

This Compose environment is isolated from production Home Assistant. Start and
stop it from the repository root:

```console
docker compose -f dev/compose.yaml up -d
docker compose -f dev/compose.yaml down --remove-orphans
```

Only Home Assistant registry and authentication state stored under
`/config/.storage` persists on the host in `dev/config/.storage`. The recorder
database, history, logs, and other generated files are intentionally disposable.
The development configuration, example secrets, and integration source are
mounted separately so Docker Desktop never receives overlapping mount targets.
