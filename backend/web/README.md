# Backend Web Layer

- API keys and configurable provider endpoints are resolved through `backend.web.config`.
- The single HTTP entry point is `backend/web/app.py`.
- Development startup is `python -m backend --mode web --port 5000` from the repository root.
- `desk_blueprint.py` mounts desktop automation APIs into the same Flask process.
