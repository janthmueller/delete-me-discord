from delete_me_discord.discord.formatting import channel_str
from delete_me_discord.privacy import RedactionConfig, set_redaction_config


def test_channel_str_redacts_name_and_id_when_enabled():
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        channel = {
            "id": "123456789012345678",
            "type": 1,
            "name": "example-user",
        }
        rendered = channel_str(channel)
    finally:
        set_redaction_config(RedactionConfig())

    assert rendered == "DM *** (ID: ***5678)"
