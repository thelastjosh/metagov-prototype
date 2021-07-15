import json
import logging
import random

from rest_framework.exceptions import ValidationError
import metagov.core.plugin_decorators as Registry
import requests
from metagov.core.errors import PluginErrorInternal
from metagov.core.models import GovernanceProcess, Plugin, ProcessStatus, AuthType

logger = logging.getLogger(__name__)


@Registry.plugin
class Slack(Plugin):
    name = "slack"
    auth_type = AuthType.OAUTH
    config_schema = {
        "type": "object",
        "properties": {
            # these are set automatically if using the oauth flow
            "team_id": {"description": "Slack Team ID", "type": "string"},
            "team_name": {"description": "Slack Team Name", "type": "string"},
            "bot_token": {"description": "Bot Token", "type": "string"},
            "bot_user_id": {"description": "Bot User ID", "type": "string"},
        },
    }

    class Meta:
        proxy = True

    @Registry.webhook_receiver(event_schemas=[])
    def receive_event(self, request):
        """
        Passes on ALL received events to the driver
        """
        json_data = json.loads(request.body)
        if json_data["type"] != "event_callback" or json_data["team_id"] != self.config["team_id"]:
            return

        # Data types: https://api.slack.com/apis/connections/events-api#the-events-api__receiving-events__events-dispatched-as-json
        # Event list: https://api.slack.com/events

        event = json_data["event"]

        # pop off 'type' and 'user' since they are represented separately in metagov-style event
        event_type = event.pop("type")
        maybe_user = event.pop("user", None)

        logger.debug(f"Received event {event_type}")

        initiator = {
            "user_id": maybe_user,
            "provider": "slack",
            "is_metagov_bot": maybe_user and maybe_user == self.config["bot_user_id"],
        }
        self.send_event_to_driver(event_type=event_type, initiator=initiator, data=event)

    @Registry.action(
        slug="post-message",
        input_schema={
            "type": "object",
            "properties": {"users": {"type": "array", "items": {"type": "string"}}, "channel": {"type": "string"}},
        },
        description="Post message either in a channel, direct message, or multi-person message. Supports all params accepted by Slack method chat.postMessage.",
    )
    def post_message(self, parameters):
        bot_token = self.config["bot_token"]
        data = {"token": bot_token, **parameters}  # note: parameters may include a token override!
        if not parameters.get("users") and not parameters.get("channel"):
            raise ValidationError("users or channel are required")
        if parameters.get("users") and not parameters.get("channel"):
            # open a conversation for DM or multi person message
            users = ",".join(parameters.get("users"))
            params = {"token": bot_token, "users": users}
            response = self.slack_request("POST", "conversations.open", data=params)
            channel = response["channel"]["id"]
            logger.debug(f"Opened conversation {channel} with users {users}")
            data["channel"] = channel
        return self.slack_request("POST", "chat.postMessage", data=data)

    def join_conversation(self, channel):
        return self.slack_request(
            "POST", "conversations.join", data={"token": self.config["bot_token"], "channel": channel}
        )

    @Registry.action(
        slug="method",
        input_schema={
            "type": "object",
            "properties": {"method_name": {"type": "string"}},
            "required": ["method_name"],
        },
        description="Perform any Slack method (provided sufficient scopes)",
    )
    def method(self, parameters):
        """
        Action for performing any method in https://api.slack.com/methods
        See also: https://api.slack.com/web#basics

        Example usage:

        curl -iX POST "https://metagov.policykit.org/api/internal/action/slack.method" -H  "accept: application/json" -H  "X-Metagov-Community: slack-tmq3pkxt9" -d '{"parameters":{"method_name":"chat.postMessage", "channel":"C0177HZTV7X", "text":"hello world"}}'
        curl -iX POST "https://metagov.policykit.org/api/internal/action/slack.method" -H  "accept: application/json" -H  "X-Metagov-Community: slack-tmq3pkxt9" -d '{"parameters":{"channel":"C0177HZTV7X","method":"pins.add","timestamp":"1622820212.008000"}}'
        curl -iX POST "https://metagov.policykit.org/api/internal/action/slack.method" -H  "accept: application/json" -H  "X-Metagov-Community: slack-tmq3pkxt9" -d '{"parameters":{"channel":"C0177HZTV7X","method":"pins.remove","timestamp":"1622820212.008000"}}'
        """
        method = parameters.pop("method_name")
        # note: parameters may include a token override!
        data = {"token": self.config["bot_token"], **parameters}
        try:
            return self.slack_request("POST", method, data=data)
        except PluginErrorInternal as e:
            # TODO: make this configurable, might not be desirable in all cases. Bot must be in the channel for `reaction.add` method to work (and others).
            if e.detail == "not_in_channel" and data.get("channel"):
                logger.warn(f"Failed with not_in_channel. Adding bot to channel {data['channel']} and retrying...")
                self.join_conversation(data["channel"])
                return self.slack_request("POST", method, data=data)
            else:
                raise

    def slack_request(self, method, route, json=None, data=None):
        url = f"https://slack.com/api/{route}"
        logger.debug(f"{method} {url}")
        resp = requests.request(method, url, json=json, data=data)
        if not resp.ok:
            logger.error(f"{resp.status_code} {resp.reason}")
            logger.error(resp.request.body)
            raise PluginErrorInternal(resp.text)
        if resp.content:
            data = resp.json()
            is_ok = data.pop("ok")
            if not is_ok:
                # logger.debug(f"X-OAuth-Scopes: {resp.headers.get('X-OAuth-Scopes')}")
                # logger.debug(f"X-Accepted-OAuth-Scopes: {resp.headers.get('X-Accepted-OAuth-Scopes')}")
                # logger.debug(data["error"])
                raise PluginErrorInternal(data["error"])
            return data
        return {}


EMOJI_MAP = {
    "numbers": [
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "keycap_ten",
    ],
    "flowers": [
        "tulip",
        "sunflower",
        "cherry_blossom",
        "rose",
        "wilted_flower",
        "bouquet",
        "hibiscus",
        "blossom",
    ],
    "hearts": [
        "blue_heart",
        "purple_heart",
        "heart",
        "green_heart",
        "sparkling_heart",
        "orange_heart",
        "green_heart",
    ],
}


class Bool:
    YES = "yes"
    NO = "no"


@Registry.governance_process
class SlackEmojiVote(GovernanceProcess):
    # TODO(enhancement): let the caller define the emoji for each option
    # TODO(enhancement): add suport for "closing_at" time
    # TODO(enhancement): support single-choice and multiple-choice
    # TODO(enhancement): only allow one vote per person on boolean votes
    name = "emoji-vote"
    plugin_name = "slack"
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "options to use for choice selection. ignored for 'boolean' poll type",
            },
            "details": {"type": "string"},
            "poll_type": {"type": "string", "enum": ["boolean", "choice"]},
            "channel": {
                "type": "string",
                "description": "channel to post the vote in",
            },
            "users": {
                "type": "array",
                "items": {"type": "string"},
                "description": "users to participate in vote in a multi-person message thread. ignored if channel is provided.",
            },
            "emoji_family": {
                "type": "string",
                "enum": ["hearts", "flowers", "numbers"],
                "description": "emoji family to use for choice selection. ignored for 'boolean' poll type",
            },
        },
        "required": ["title", "poll_type"],
    }

    class Meta:
        proxy = True

    def start(self, parameters) -> None:
        text = construct_message_header(parameters["title"], parameters.get("details"))
        self.state.set("message_header", text)
        poll_type = parameters["poll_type"]
        options = [Bool.YES, Bool.NO] if poll_type == "boolean" else parameters["options"]
        if options is None:
            raise ValidationError("Options are required for non-boolean votes")

        maybe_channel = parameters.get("channel")
        maybe_users = parameters.get("users")
        if maybe_channel is None and (maybe_users is None or len(maybe_users) == 0):
            raise ValidationError("users or channel are required")

        if poll_type == "boolean":
            option_emoji_map = {"+1": Bool.YES, "-1": Bool.NO}
        else:
            family = parameters.get("emoji_family", "numbers")
            emojis = EMOJI_MAP[family]
            if len(emojis) < len(options):
                raise PluginErrorInternal("There are more voting options than possible emojis")
            if family != "numbers":
                random.shuffle(emojis)
            emojis = emojis[: len(options)]
            option_emoji_map = dict(zip(emojis, options))
            for (k, v) in option_emoji_map.items():
                text += f"\n> :{k}:  {v}"

        self.state.set("option_emoji_map", option_emoji_map)

        response = self.plugin_inst.post_message({"channel": maybe_channel, "users": maybe_users, "text": text})
        ts = response["ts"]
        channel = response["channel"]

        permalink_resp = self.plugin_inst.method(
            {
                "method_name": "chat.getPermalink",
                "channel": channel,
                "message_ts": ts,
            }
        )

        # Add 1 initial reaction for each emoji type
        for emoji in option_emoji_map.keys():
            self.plugin_inst.method(
                {"method_name": "reactions.add", "channel": channel, "timestamp": ts, "name": emoji}
            )

        self.state.set("poll_type", parameters["poll_type"])

        self.outcome = {
            "url": permalink_resp["permalink"],
            "channel": channel,
            "message_ts": ts,
            "votes": dict([(k, {"users": [], "count": 0}) for k in options]),
        }
        self.status = ProcessStatus.PENDING.value
        self.save()

    def receive_webhook(self, request):
        json_data = json.loads(request.body)
        data = json_data["event"]
        evt_type = data["type"]
        if not evt_type.startswith("reaction_"):
            return
        ts = data["item"]["ts"]
        if ts != self.outcome["message_ts"]:
            return

        reaction = normalize_reaction(data["reaction"])
        option_emoji_map = self.state.get("option_emoji_map")
        if reaction not in option_emoji_map:
            return

        option = option_emoji_map[reaction]
        logger.debug(f"Processing reaction '{reaction}' as a vote for '{option}'")

        # Get the voting message post and update all the vote counts based on the emojis currently present
        ts = self.outcome["message_ts"]
        response = self.plugin_inst.method(
            {
                "method_name": "conversations.history",
                "channel": self.outcome["channel"],
                "latest": ts,
                "oldest": ts,
                "inclusive": True,
                "limit": 1,
            }
        )
        self.update_outcome_from_reaction_list(response["messages"][0].get("reactions", []))

    def close(self):
        # Edit content of the post to mark it as "closed."
        option_emoji_map = self.state.get("option_emoji_map")
        text = self.state.get("message_header")
        if self.state.get("poll_type") == "boolean":
            yes = self.outcome["votes"][Bool.YES]["count"]
            no = self.outcome["votes"][Bool.NO]["count"]
            text += f"\nFinal vote count: {yes} for and {no} against."
        else:
            for (k, v) in option_emoji_map.items():
                count = self.outcome["votes"][v]["count"]
                text += f"\n> :{k}:  {v} ({count})"

        self.plugin_inst.method(
            {
                "method_name": "chat.update",
                "channel": self.outcome["channel"],
                "ts": self.outcome["message_ts"],
                "text": text,
            }
        )
        self.status = ProcessStatus.COMPLETED.value
        self.save()

    def update_outcome_from_reaction_list(self, reaction_list):
        self.outcome["votes"] = reactions_to_dict(
            reaction_list, self.state.get("option_emoji_map"), excluded_users=[self.plugin_inst.config["bot_user_id"]]
        )
        self.save()


def construct_message_header(title, details=None):
    text = f"*{title}*\n"
    if details:
        text += f"{details}\n"
    return text


def reactions_to_dict(reaction_list, emoji_to_option, excluded_users=[]):
    """Convert list of reactions from Slack API into a dictionary of option votes"""
    votes = {}
    for r in reaction_list:
        emoji = normalize_reaction(r.pop("name"))
        option = emoji_to_option.get(emoji)
        if not option:
            continue
        # remove excluded users from list of reactions
        user_list = set(r["users"])
        user_list.difference_update(set(excluded_users))
        user_list = list(user_list)
        user_list.sort()

        if votes.get(option):
            # we already have some users listed (because of normalized reactions)
            uniq_users = list(set(votes[option]["users"] + user_list))
            uniq_users.sort()
            votes[option] = {"users": uniq_users, "count": len(uniq_users)}
        else:
            votes[option] = {"users": user_list, "count": len(user_list)}

    # add zeros for options that don't have any reactions
    for v in emoji_to_option.values():
        if votes.get(v) is None:
            votes[v] = {"users": [], "count": 0}

    return votes


def normalize_reaction(reaction: str):
    if reaction.startswith("+1::skin-tone-"):
        return "+1"
    if reaction.startswith("-1::skin-tone-"):
        return "-1"
    return reaction
