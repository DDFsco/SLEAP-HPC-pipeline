"""Compatibility patches loaded only for locally launched SLEAP GUI."""
from __future__ import annotations


def _drop_key(config, dotted_key: str) -> None:
    current = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        try:
            current = current.get(part)
        except AttributeError:
            current = getattr(current, part, None)
        if current is None:
            return
    try:
        current.pop(parts[-1], None)
    except AttributeError:
        try:
            delattr(current, parts[-1])
        except AttributeError:
            pass


try:
    import sleap_nn.config.training_job_config as training_job_config

    _original_verify_training_cfg = training_job_config.verify_training_cfg

    def verify_training_cfg_without_legacy_keys(cfg):
        _drop_key(cfg, "data_config.use_negative_frames")
        return _original_verify_training_cfg(cfg)

    training_job_config.verify_training_cfg = verify_training_cfg_without_legacy_keys
except Exception:
    pass
