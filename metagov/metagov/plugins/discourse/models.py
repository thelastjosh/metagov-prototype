import hashlib
import hmac
import json
import logging

import metagov.core.plugin_decorators as Registry
import metagov.plugins.discourse.schemas as Schemas
import requests
from metagov.core.errors import PluginErrorInternal
from metagov.core.models import GovernanceProcess, Plugin, AuthType, ProcessStatus

logger = logging.getLogger(__name__)

EVENT_POST_CREATED = "post_created"
EVENT_TOPIC_CREATED = "topic_created"
EVENT_USER_FIELDS_CHANGED = "user_fields_changed"

"""
TODO: add actions and events for "user actions":
 LIKE = 1
 WAS_LIKED = 2
 BOOKMARK = 3
 NEW_TOPIC = 4
 REPLY = 5
 RESPONSE= 6
 MENTION = 7
 QUOTE = 9
 EDIT = 11
 NEW_PRIVATE_MESSAGE = 12
 GOT_PRIVATE_MESSAGE = 13
"""


@Registry.plugin
class Discourse(Plugin):
    name = "discourse"
    auth_type = AuthType.API_KEY
    config_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "api_key": {
                "type": "string",
                "description": "Discourse API key for a bot user that is an admin. Actions will be taken on behalf of this user.",
            },
            "server_url": {"type": "string", "description": "URL of the Discourse server"},
            "webhook_secret": {
                "type": "string",
                "description": "A random string. When creating the Metagov webhook in Discourse, enter this string under 'secret.'",
            },
            "webhook_slug": {"type": "string"},
        },
        "required": ["api_key", "server_url", "webhook_secret"],
    }

    class Meta:
        proxy = True

    def initialize(self):
        resp = requests.get(f"{self.config['server_url']}/about.json")
        response = resp.json()
        community_name = response.get("about").get("title")
        logger.info(f"Initialized Discourse plugin for community {community_name}")
        self.state.set("community_name", community_name)
        self.store_user_list()

    def construct_post_url(self, post):
        return f"{self.config['server_url']}/t/{post['topic_slug']}/{post['topic_id']}/{post['post_number']}"

    def construct_topic_url(self, topic):
        return f"{self.config['server_url']}/t/{topic['slug']}/{topic['id']}"

    def construct_post_response(self, post):
        return {"url": self.construct_post_url(post), "topic_id": post["topic_id"], "post_id": post["id"]}

    def discourse_request(self, method, route, json=None, data=None):
        url = f"{self.config['server_url']}/{route}"
        logger.info(f"{method} {url}")

        headers = {"Api-Key": self.config["api_key"]}
        resp = requests.request(method, url, headers=headers, json=json, data=data)
        if not resp.ok:
            logger.error(f"{resp.status_code} {resp.reason}")
            logger.error(resp.request.body)
            raise PluginErrorInternal(resp.text)
        if resp.content:
            return resp.json()
        return None

    @Registry.action(
        slug="create-message",
        description="Start a new private message thread",
        input_schema=Schemas.send_message_parameters,
        output_schema=Schemas.create_post_or_topic_response,
    )
    def create_message(self, parameters):
        parameters["target_recipients"] = ",".join(parameters.pop("target_usernames"))
        if parameters.get("topic_id"):
            parameters["archetype"] = "regular"
        else:
            parameters["archetype"] = "private_message"
        post = self.discourse_request("POST", "posts.json", json=parameters)
        return self.construct_post_response(post)

    @Registry.action(
        slug="create-post",
        description="Create a new post",
        input_schema=Schemas.create_post_parameters,
        output_schema=Schemas.create_post_or_topic_response,
    )
    def create_post(self, parameters):
        post = self.discourse_request("POST", "posts.json", json=parameters)
        return self.construct_post_response(post)

    @Registry.action(
        slug="create-topic",
        description="Create a new topic",
        input_schema=Schemas.create_topic_parameters,
        output_schema=Schemas.create_post_or_topic_response,
    )
    def create_topic(self, parameters):
        post = self.discourse_request("POST", "posts.json", json=parameters)
        return self.construct_post_response(post)

    @Registry.action(
        slug="delete-post",
        description="Delete a post",
        input_schema=Schemas.delete_post_or_topic_parameters,
        output_schema=None,
    )
    def delete_post(self, parameters):
        self.discourse_request("DELETE", f"posts/{parameters['id']}")
        return {}

    @Registry.action(
        slug="delete-topic",
        description="Delete a topic",
        input_schema=Schemas.delete_post_or_topic_parameters,
        output_schema=None,
    )
    def delete_topic(self, parameters):
        self.discourse_request("DELETE", f"t/{parameters['id']}.json")
        return {}

    @Registry.action(
        slug="recover-post",
        description="Recover a deleted post",
        input_schema=Schemas.delete_post_or_topic_parameters,
        output_schema=None,
    )
    def recover_post(self, parameters):
        self.discourse_request("PUT", f"posts/{parameters['id']}/recover")
        return {}

    @Registry.action(
        slug="recover-topic",
        description="Recover a deleted topic",
        input_schema=Schemas.delete_post_or_topic_parameters,
        output_schema=None,
    )
    def recover_topic(self, parameters):
        self.discourse_request("PUT", f"t/{parameters['id']}/recover")
        return {}

    @Registry.action(
        slug="lock-post",
        description="Lock or unlock a post on discourse",
        input_schema=Schemas.lock_post_parameters,
        output_schema=Schemas.lock_post_response,
    )
    def lock_post(self, parameters):
        post_id = parameters["id"]
        data = {"locked": json.dumps(parameters["locked"])}
        return self.discourse_request("PUT", f"posts/{post_id}/locked", data=data)

    def validate_request_signature(self, request):
        event_signature = request.headers.get("X-Discourse-Event-Signature")
        if not event_signature:
            raise PluginErrorInternal("Missing event signature")
        key = bytes(self.config["webhook_secret"], "utf-8")
        string_signature = hmac.new(key, request.body, hashlib.sha256).hexdigest()
        expected_signature = f"sha256={string_signature}"
        if not hmac.compare_digest(event_signature, expected_signature):
            raise PluginErrorInternal("Invalid signature header")

        instance = request.headers["X-Discourse-Instance"]
        if instance != self.config["server_url"]:
            raise PluginErrorInternal("Unexpected X-Discourse-Instance")

    def store_user_list(self):
        # TODO paginate request
        response = self.discourse_request("GET", f"admin/users/list/active.json")
        logger.info(f"Fetching {len(response)} users...")
        users = {}
        for user in response:
            id = str(user["id"])
            users[id] = self.discourse_request("GET", f"admin/users/{id}.json")
        self.state.set("users", users)
        logger.info(f"Saved {len(response)} users in state.")

    @Registry.webhook_receiver(
        event_schemas=[
            {"type": EVENT_POST_CREATED, "schema": Schemas.post_topic_created_event},
            {"type": EVENT_TOPIC_CREATED, "schema": Schemas.post_topic_created_event},
            {"type": EVENT_USER_FIELDS_CHANGED},
        ]
    )
    def process_discourse_webhook(self, request):
        self.validate_request_signature(request)
        event = request.headers.get("X-Discourse-Event")
        body = json.loads(request.body)
        logger.info(f"Received event '{event}' from Discourse")

        if event == "post_created":
            post = body.get("post")
            data = {
                "raw": post["raw"],
                "topic_id": post["topic_id"],
                "id": post["id"],
                "url": self.construct_post_url(post),
            }
            initiator = {"user_id": post["username"], "provider": "discourse"}
            self.send_event_to_driver(event_type=EVENT_POST_CREATED, initiator=initiator, data=data)
        elif event == "topic_created":
            topic = body.get("topic")
            data = {
                "title": topic["title"],
                "id": topic["id"],
                "tags": topic["tags"],
                "category": topic["category_id"],
                "url": self.construct_topic_url(topic),
            }
            initiator = {"user_id": topic["created_by"]["username"], "provider": "discourse"}
            self.send_event_to_driver(event_type=EVENT_TOPIC_CREATED, initiator=initiator, data=data)
        elif event == "user_updated":
            updated_user = body.get("user")

            # Get the old user record from state
            user_map = self.state.get("users")
            user_id = str(updated_user["id"])
            old_user = user_map.get(user_id)

            # Update state so that we have the latest user map
            user_map[user_id] = updated_user
            self.state.set("users", user_map)

            # if `user_fields` changed, send an event to the Driver
            if not old_user or old_user["user_fields"] != updated_user["user_fields"]:
                data = {
                    "id": updated_user["id"],
                    "username": updated_user["username"],
                    "user_fields": updated_user["user_fields"],
                    "old_user_fields": old_user["user_fields"] if old_user else None,
                }
                initiator = {"user_id": updated_user["username"], "provider": "discourse"}
                self.send_event_to_driver(event_type=EVENT_USER_FIELDS_CHANGED, initiator=initiator, data=data)


"""
GOVERNANCE PROCESSES
"""


@Registry.governance_process
class DiscoursePoll(GovernanceProcess):
    name = "poll"
    plugin_name = "discourse"

    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "details": {"type": "string"},
            "topic_id": {"type": "integer", "description": "required if creating the poll as a new post."},
            "category": {
                "type": "integer",
                "description": "optional if creating the poll as a new topic, and ignored if creating it as a new post.",
            },
            "closing_at": {"type": "string", "format": "date"},
            "poll_type": {"type": "string", "enum": ["regular", "multiple", "number"]},
            "public": {"type": "boolean", "description": "whether votes are public"},
            "results": {
                "type": "string",
                "enum": ["always", "on_vote", "on_close", "staff_only"],
                "description": "when to show results",
            },
            "min": {
                "type": "integer",
                "description": "Must be at least 1. For 'number' poll type, this is the minimum number. For 'multiple' poll type, this is the minumum number of options that a user can vote for. For 'regular' poll type, this option is ignored.",
            },
            "max": {
                "type": "integer",
                "description": "Must be at least 1, but less than or equal with the number of options. For 'number' poll type, this is the maximum number. For 'multiple' poll type, this is the maximum number of options that a user can vote for. For 'regular' poll type, this option is ignored.",
            },
            "step": {
                "type": "integer",
                "description": "For 'number' poll type, the step in between numbers. Ignored for other poll types. The minimum step value is 1.",
            },
            "chart_type": {"type": "string", "enum": ["pie", "bar"]},
            "groups": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    }
    # TODO: define outcome schema

    class Meta:
        proxy = True

    def start(self, parameters) -> None:
        discourse_server_url = self.plugin_inst.config["server_url"]
        url = f"{discourse_server_url}/posts.json"

        poll_type = parameters.get("poll_type", "regular")
        if poll_type != "number" and not len(parameters.get("options", [])):
            raise PluginErrorInternal(f"Options are required for poll type {poll_type}")

        optional_params = []
        if parameters.get("closing_at"):
            optional_params.append(f"close={parameters['closing_at']}")
        if parameters.get("groups"):
            optional_params.append(f"groups={','.join(parameters['groups'])}")
        if parameters.get("public") is True:
            optional_params.append("public=true")
        if parameters.get("chart_type"):
            optional_params.append(f"chartType={parameters['chart_type']}")
        for p in ["min", "max", "step", "results"]:
            if parameters.get(p) is not None:
                optional_params.append(f"{p}={parameters[p]}")

        options = "".join([f"* {opt}\n" for opt in parameters["options"]]) if poll_type != "number" else ""
        raw = f"""
{parameters.get("details") or ""}
[poll type={poll_type} {' '.join(optional_params)}]
# {parameters["title"]}
{options}
[/poll]
        """
        payload = {"raw": raw, "title": parameters["title"]}
        if parameters.get("category"):
            payload["category"] = parameters["category"]
        if parameters.get("topic_id"):
            payload["topic_id"] = parameters["topic_id"]

        logger.info(payload)
        logger.info(url)

        response = self.plugin_inst.discourse_request("POST", "posts.json", json=payload)
        if response.get("errors"):
            errors = response["errors"]
            raise PluginErrorInternal(str(errors))

        poll_url = self.plugin_inst.construct_post_url(response)
        logger.info(f"Poll created at {poll_url}")
        self.state.set("post_id", response.get("id"))
        self.state.set("topic_id", response.get("topic_id"))
        self.state.set("topic_slug", response.get("topic_slug"))

        self.outcome = {"poll_url": poll_url}  # this gets serialized and returned
        self.status = ProcessStatus.PENDING.value
        self.save()

    def update(self):
        """
        We make a request to Discourse EVERY time, here, so that we can catch cases where the poll was closed
        manually by a user. Would be simplified if we disallow that, and instead this function could just
        check if `closing_at` has happened yet (if set) and call close() if it has.
        """
        post_id = self.state.get("post_id")
        response = self.plugin_inst.discourse_request("GET", f"posts/{post_id}.json")
        poll = response["polls"][0]
        self.update_outcome_from_discourse_poll(poll)

    def close(self):
        """
        Invoked by the Driver to manually close the poll. This would be used in cases where `closing_at` param is not set,
        or in cases where the Driver wants to close the poll early (before closing_at time).
        """
        post_id = self.state.get("post_id")
        data = {"post_id": post_id, "poll_name": "poll", "status": "closed"}
        response = self.plugin_inst.discourse_request("PUT", "polls/toggle_status", data=data)
        poll = response["poll"]
        self.update_outcome_from_discourse_poll(poll)

        # Lock the post
        # self.plugin_inst.lock_post({"locked": True, "id": post_id})

    def update_outcome_from_discourse_poll(self, poll):
        """Save changes to outcome and state if changed"""
        dirty = False
        votes = self.outcome.get("votes", {})
        for opt in poll["options"]:
            key = opt["html"]
            val = opt["votes"]
            if votes.get(key) != val:
                votes[key] = val
                dirty = True

        if poll["status"] == "closed":
            self.status = ProcessStatus.COMPLETED.value
            dirty = True

        if dirty:
            logger.info(f"{self}: {self.outcome}")
            self.outcome["votes"] = votes
            self.save()
