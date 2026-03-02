from enum import IntEnum

# Added from https://docs.discord.food/resources/message
# https://discordhttp.alexflipnote.dev/api/enums.html#discord_http.enums.MessageType.custom_gift
class MessageType(IntEnum):
    DEFAULT = 0, True
    RECIPIENT_ADD = 1, False
    RECIPIENT_REMOVE = 2, False
    CALL = 3, False
    CHANNEL_NAME_CHANGE = 4, False
    CHANNEL_ICON_CHANGE = 5, False
    CHANNEL_PINNED_MESSAGE = 6, True
    USER_JOIN = 7, True
    GUILD_BOOST = 8, True
    GUILD_BOOST_TIER_1 = 9, True
    GUILD_BOOST_TIER_2 = 10, True
    GUILD_BOOST_TIER_3 = 11, True
    CHANNEL_FOLLOW_ADD = 12, True
    # GUILD_STREAM = 13, True  # Deprecated/unused per docs.discord.food.
    GUILD_DISCOVERY_DISQUALIFIED = 14, True
    GUILD_DISCOVERY_REQUALIFIED = 15, True
    GUILD_DISCOVERY_GRACE_PERIOD_INITIAL_WARNING = 16, True
    GUILD_DISCOVERY_GRACE_PERIOD_FINAL_WARNING = 17, True
    THREAD_CREATED = 18, True
    REPLY = 19, True
    CHAT_INPUT_COMMAND = 20, True
    THREAD_STARTER_MESSAGE = 21, False
    GUILD_INVITE_REMINDER = 22, True
    CONTEXT_MENU_COMMAND = 23, True
    AUTO_MODERATION_ACTION = 24, False  # Requires special permissions
    ROLE_SUBSCRIPTION_PURCHASE = 25, True
    INTERACTION_PREMIUM_UPSELL = 26, True
    STAGE_START = 27, True
    STAGE_END = 28, True
    STAGE_SPEAKER = 29, True
    STAGE_RAISE_HAND = 30, True
    STAGE_TOPIC = 31, True
    GUILD_APPLICATION_PREMIUM_SUBSCRIPTION = 32, True
    # PRIVATE_CHANNEL_INTEGRATION_ADDED = 33, False  # Deprecated/unused per docs.discord.food.
    # PRIVATE_CHANNEL_INTEGRATION_REMOVED = 34, False  # Deprecated/unused per docs.discord.food.
    PREMIUM_REFERRAL = 35, True
    GUILD_INCIDENT_ALERT_MODE_ENABLED = 36, True
    GUILD_INCIDENT_ALERT_MODE_DISABLED = 37, True
    GUILD_INCIDENT_REPORT_RAID = 38, True
    GUILD_INCIDENT_REPORT_FALSE_ALARM = 39, True
    GUILD_DEADCHAT_REVIVE_PROMPT = 40, True
    CUSTOM_GIFT = 41, True
    GUILD_GAMING_STATS_PROMPT = 42, True
    # POLL = 43, True  # Deprecated/unused per docs.discord.food.
    PURCHASE_NOTIFICATION = 44, True
    # VOICE_HANGOUT_INVITE = 45, True  # Deprecated/unused per docs.discord.food.
    POLL_RESULT = 46, True
    CHANGELOG = 47, True
    NITRO_NOTIFICATION = 48, True
    CHANNEL_LINKED_TO_LOBBY = 49, True
    GIFTING_PROMPT = 50, True
    IN_GAME_MESSAGE_NUX = 51, True
    GUILD_JOIN_REQUEST_ACCEPT_NOTIFICATION = 52, True
    GUILD_JOIN_REQUEST_REJECT_NOTIFICATION = 53, True
    GUILD_JOIN_REQUEST_WITHDRAWN_NOTIFICATION = 54, True
    HD_STREAMING_UPGRADED = 55, True
    # CHAT_WALLPAPER_SET = 56, False  # Deprecated/unused per docs.discord.food.
    # CHAT_WALLPAPER_REMOVE = 57, False  # Deprecated/unused per docs.discord.food.
    REPORT_TO_MOD_DELETED_MESSAGE = 58, True
    REPORT_TO_MOD_TIMEOUT_USER = 59, True
    REPORT_TO_MOD_KICK_USER = 60, True
    REPORT_TO_MOD_BAN_USER = 61, True
    REPORT_TO_MOD_CLOSED_REPORT = 62, True
    EMOJI_ADDED = 63, True

    def __new__(cls, value: int, deletable: bool):
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj._deletable = deletable
        return obj

    @property
    def deletable(self):
        return self._deletable

    def __str__(self):
        return f"{self.name} (Value: {self.value}, Deletable: {self.deletable})"

DELETABLE_MESSAGE_TYPES = [message_type for message_type in MessageType if message_type.deletable]

if __name__ == "__main__":
    for message_type in MessageType:
        print(message_type)
