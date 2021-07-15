import json
import logging

import metagov.core.plugin_decorators as Registry
import metagov.plugins.loomio.schemas as Schemas
import requests
from metagov.core.errors import PluginErrorInternal
from metagov.core.models import GovernanceProcess, Plugin, ProcessStatus, AuthType

logger = logging.getLogger(__name__)


@Registry.plugin
class Loomio(Plugin):
    name = "loomio"
    auth_type = AuthType.API_KEY
    config_schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string"}, "webhook_slug": {"type": "string"}},
        "required": ["api_key"],
    }

    class Meta:
        proxy = True

    def initialize(self):
        pass

    @Registry.action(slug="list-members", description="list groups and users")
    def list_members(self, _parameters):
        resp = requests.get(f"https://www.loomio.org/api/b1/memberships?api_key={self.config['api_key']}")
        if not resp.ok:
            logger.error(f"Error: {resp.status_code} {resp.text}")
            raise PluginErrorInternal(resp.text)
        return resp.json()

    @Registry.action(
        slug="create-discussion",
        description="create a new discussion",
        input_schema=Schemas.create_discussion_input,
    )
    def create_discussion(self, parameters):
        payload = parameters
        payload["api_key"] = self.config["api_key"]
        resp = requests.post(f"https://www.loomio.org/api/b1/discussions", payload)
        if not resp.ok:
            logger.error(f"Error: {resp.status_code} {resp.text}")
            raise PluginErrorInternal(resp.text)
        response = resp.json()
        return response

    @Registry.webhook_receiver()
    def loomio_webhook(self, request):
        pass

@Registry.governance_process
class LoomioPoll(GovernanceProcess):
    name = "poll"
    plugin_name = "loomio"
    input_schema = Schemas.start_loomio_poll

    class Meta:
        proxy = True

    def start(self, parameters):
        url = "https://www.loomio.org/api/b1/polls"

        options = parameters.pop("options")
        payload = {
            **parameters,
            "options[]": options,
            "api_key": self.plugin_inst.config["api_key"],
        }

        resp = requests.post(url, payload)
        if not resp.ok:
            logger.error(f"Error: {resp.status_code} {resp.text}")
            raise PluginErrorInternal(resp.text)

        response = resp.json()

        if response.get("errors"):
            errors = response["errors"]
            raise PluginErrorInternal(str(errors))

        poll_key = response.get("polls")[0].get("key")
        poll_url = f"https://www.loomio.org/p/{poll_key}"
        self.state.set("poll_key", poll_key)
        self.outcome = {"poll_url": poll_url}
        self.status = ProcessStatus.PENDING.value
        self.save()

    def receive_webhook(self, request):
        poll_key = self.state.get("poll_key")
        poll_url = self.outcome.get("poll_url")

        body = json.loads(request.body)
        url = body.get("url")
        if url is None or not url.startswith(poll_url):
            return
        kind = body.get("kind")
        logger.info(f"Processing event '{kind}' for poll {url}")
        if kind == "poll_closed_by_user" or kind == "poll_expired":
            logger.info(f"Loomio poll closed. Fetching poll result...")
            self.fetch_and_update_outcome()
            assert self.status == ProcessStatus.COMPLETED.value
        elif kind == "stance_created":
            # update each time a vote is cast, so that the driver can decide when to close the vote based on threshold if desired
            self.fetch_and_update_outcome()

    def fetch_and_update_outcome(self):
        poll_key = self.state.get("poll_key")
        url = f"https://www.loomio.org/api/b1/polls/{poll_key}?api_key={self.plugin_inst.config['api_key']}"
        resp = requests.get(url)
        if not resp.ok:
            logger.error(f"Error fetching poll: {resp.status_code} {resp.text}")
            raise PluginErrorInternal(resp.text)

        response = resp.json()
        if response.get("errors"):
            logger.error(f"Error fetching poll outcome: {response['errors']}")
            self.errors = response["errors"]

        poll = response.get("polls")[0]

        self.outcome["votes"] = poll.get("stance_data")

        if poll.get("closed_at") is not None:
            self.status = ProcessStatus.COMPLETED.value

        logger.info(f"{self}: {self.outcome}")
        self.save()
