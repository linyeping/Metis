DEFAULT_CONFIG = {
    "retry_count": 3,
    "enable_empty_records": True,
}


def load_config() -> dict:
    return dict(DEFAULT_CONFIG)
