from hermes_cli.config import DEFAULT_CONFIG


def test_affinda_compression_defaults_are_cost_aware_and_non_destructive():
    compression = DEFAULT_CONFIG["compression"]
    assert compression["enabled"] is True
    assert compression["threshold"] == 0.7
    assert compression["target_ratio"] == 0.2
    assert compression["abort_on_summary_failure"] is True

    auxiliary_compression = DEFAULT_CONFIG["auxiliary"]["compression"]
    assert auxiliary_compression["provider"] == "openai-codex"
    assert auxiliary_compression["model"] == "gpt-5.5"
    assert auxiliary_compression["timeout"] == 120
    assert auxiliary_compression["fallback_chain"] == [
        {"provider": "anthropic", "model": "claude-opus-4-7"},
    ]


def test_affinda_gateway_update_defaults_reduce_noise_and_keep_backups():
    assert DEFAULT_CONFIG["agent"]["gateway_notify_interval"] == 600
    assert DEFAULT_CONFIG["updates"]["pre_update_backup"] is True
    assert DEFAULT_CONFIG["updates"]["backup_keep"] == 5


def test_affinda_memory_tiny_defaults_are_compact():
    memory = DEFAULT_CONFIG["memory"]
    assert memory["memory_char_limit"] == 2200
    assert memory["user_char_limit"] == 1375
