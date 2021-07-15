import requests, json, logging
from collections import Counter

import metagov.core.plugin_decorators as Registry
from metagov.core.models import Plugin, GovernanceProcess, ProcessStatus
from metagov.core.errors import PluginErrorInternal
import metagov.plugins.github.schemas as Schemas
from metagov.plugins.github.utils import (create_issue_text, close_comment_vote_text,
    close_react_vote_text, get_jwt)

logger = logging.getLogger(__name__)


@Registry.plugin
class Github(Plugin):
    name = 'github'
    config_schema = Schemas.github_app_config_schema

    class Meta:
        proxy = True

    def refresh_token(self):
        """Requests a new installation access token from Github using a JWT signed by private key."""

        # for now, we assume user manually installs apps, gets ID, and adds it in plugin schema
        installation_id = self.config["installation_id"]
        self.state.set("installation_id", installation_id)

        # get installation access token using installation_id
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {get_jwt()}"
        }
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        resp = requests.request("POST", url, headers=headers)

        if not resp.ok:
            logger.error(f"Error refreshing token: status {resp.status_code}, details: {resp.text}")
            raise PluginErrorInternal(resp.text)
        if resp.content:
            token = resp.json()["token"]
            self.state.set("installation_access_token", token)

    def initialize(self):
        self.refresh_token()
        logger.info(f"Initialized Slack Plugin for community with installation ID {self.config['installation_id']}")

    def parse_github_webhook(self, request):

        if 'payload' in request.POST:
            body = json.loads(request.POST['payload'])
        else:
            body = json.loads(request.body)

        action_target_type = request.headers["X_GITHUB_EVENT"]
        action_type = body["action"]
        initiator = {"user_id": body["sender"]["id"], "user_name": body["sender"]["login"], "provider": "github"}

        return action_type, action_target_type, initiator, body

    @Registry.webhook_receiver()
    def github_webhook_receiver(self, request):
        action_type, action_target_type, initiator, body = self.parse_github_webhook(request)
        logger.info(f"Received webhook event '{action_type} {action_target_type}' by user {initiator['user_id']}")
        self.send_event_to_driver(event_type=f"{action_type} {action_target_type}", data=body, initiator=initiator)

    def github_request(self, method, route, data=None, add_headers=None, refresh=False):
        """Makes request to Github. If status code returned is 401 (bad credentials), refreshes the
        access token and tries again. Refresh parameter is used to make sure we only try once."""

        headers = {
            "Authorization": f"token {self.state.get('installation_access_token')}",
            "Accept": "application/vnd.github.v3+json"
        }
        if add_headers:
            headers.update(add_headers)

        url = f"https://api.github.com{route}"
        logger.info(f"Making request {method} to {route}")
        resp = requests.request(method, url, headers=headers, json=data)

        if resp.status_code == 401 and refresh == False:
            logger.info(f"Bad credentials, refreshing token and retrying")
            self.refresh_token()
            return self.github_request(method=method, route=route, data=data, add_header=add_headers, refresh=True)
        if not resp.ok:
            logger.error(f"Request error for {method}, {route}; status {resp.status_code}, details: {resp.text}")
            raise PluginErrorInternal(resp.text)
        if resp.content:
            return resp.json()
        return None

    @Registry.action(
        slug='get-issues',
        description='gets all issues in a repository',
        input_schema=Schemas.get_issues_parameters,
        output_schema=None
    )
    def get_issues(self, parameters):  # TODO: replace with something like the Slack plugin's method method
        owner, repo = self.config["owner"], parameters["repo_name"]
        issues = self.github_request(method="get", route=f"/repos/{owner}/{repo}/issues")
        return {"issue_count": len(issues), "issues": issues}

    @Registry.action(
        slug='create-issue',
        description='creates issue in a repository',
        input_schema=Schemas.create_issue_parameters,
        output_schema=None
    )
    def create_issue(self, parameters):  # TODO: replace with something like the Slack plugin's method method
        owner, repo = self.config["owner"], parameters["repo_name"]
        data = {"title": parameters["title"], "body": parameters["body"]}
        return self.github_request(method="post", route=f"/repos/{owner}/{repo}/issues", data=data)


"""
GOVERNANCE PROCESSES
"""

@Registry.governance_process
class GithubIssueReactVote(GovernanceProcess):
    name = 'github-issue-react-vote'
    plugin_name = 'github'
    input_schema = Schemas.issue_react_vote_parameters

    class Meta:
        proxy = True

    def start(self, parameters):

        # copy owner & repo to state
        self.state.set("owner", self.plugin_inst.config["owner"])
        self.state.set("repo", parameters["repo_name"])
        self.state.set("max_votes", parameters["max_votes"])

        # create an issue to use as a vote:
        parameters["title"], parameters["body"] = create_issue_text("react", parameters)
        issue = self.plugin_inst.create_issue(parameters=parameters)

        self.state.set("issue_number", issue["number"])
        self.status = ProcessStatus.PENDING.value
        self.save()
        logger.info(f"Starting IssueReactVote with issue # {issue['number']}")

    def get_basic_info(self):
        return self.state.get("owner"), self.state.get("repo"), self.state.get("issue_number")

    def get_vote_data(self):

        owner, repo, issue_number = self.get_basic_info()

        # Get issue react count
        headers = {"Accept": "application/vnd.github.squirrel-girl-preview"}
        reactions = self.plugin_inst.github_request(
            method="get", route=f"/repos/{owner}/{repo}/issues/{issue_number}/reactions", add_headers=headers)

        upvotes, downvotes = 0, 0
        for reaction in reactions:
            if reaction["content"] == "+1":
                upvotes += 1
            if reaction["content"] == "-1":
                downvotes += 1

        if upvotes > downvotes:
            result = "pass"
        elif downvotes > upvotes:
            result = "fail"
        else:
            result = "tie"

        return upvotes, downvotes, result

    def close_vote(self, upvotes, downvotes, result):

        # Add a comment to the issue declaring the vote closed and showing the results.
        owner, repo, issue_number = self.get_basic_info()
        close_text = close_react_vote_text(upvotes, downvotes, result)
        self.plugin_inst.github_request(method="post", data={"body": close_text},
            route=f"/repos/{owner}/{repo}/issues/{issue_number}/comments")

        # Close the issue
        self.plugin_inst.github_request(
            method="post", route=f"/repos/{owner}/{repo}/issues/{issue_number}", data={"state": "closed"})

    def close(self):
        upvotes, downvotes, result = self.get_vote_data()
        self.close_vote(upvotes, downvotes, result)
        self.status = ProcessStatus.COMPLETED.value
        self.save()
        owner, repo, issue_number = self.get_basic_info()
        logger.info(f"Closing IssueReactVote {owner}/{repo} - issue # {issue_number}")

    def update(self):
        upvotes, downvotes, result = self.get_vote_data()
        max_votes = self.state.get("max_votes")
        if max_votes and (upvotes + downvotes >= max_votes):
            self.close()
        owner, repo, issue_number = self.get_basic_info()
        logger.info(f"Updating IssueReactVote {owner}/{repo} - issue # {issue_number}")


@Registry.governance_process
class GithubIssueCommentVote(GovernanceProcess):
    name = 'github-issue-comment-vote'
    plugin_name = 'github'
    input_schema = Schemas.issue_comment_vote_parameters

    class Meta:
        proxy = True

    def start(self, parameters):

        # copy owner & repo to state
        self.state.set("owner", self.plugin_inst.config["owner"])
        self.state.set("repo", parameters["repo_name"])
        self.state.set("max_votes", parameters["max_votes"])

        # create an issue to use as a vote:
        parameters["title"], parameters["body"] = create_issue_text("comment", parameters)
        issue = self.plugin_inst.create_issue(parameters=parameters)

        # save
        self.state.set("issue_number", issue["number"])
        self.status = ProcessStatus.PENDING.value
        self.save()
        logger.info(f"Starting IssueCommentVote with issue # {issue['number']}")

    def get_basic_info(self):
        return self.state.get("owner"), self.state.get("repo"), self.state.get("issue_number")

    def get_vote_data(self):
        """Gets vote data from issue comments, looking for text between strings _VOTE_ and _ENDVOTE_.
        Only counts the first comment for a given user. Not case sensitive, and removes spaces."""

        owner, repo, issue_number = self.get_basic_info()

        # Get issue comments
        comments = self.plugin_inst.github_request(
            method="get", route=f"/repos/{owner}/{repo}/issues/{issue_number}/comments")

        voter_list = []  #
        votes = Counter()
        for comment in comments:
            body, user = comment["body"], comment["user"]["login"]
            if user in voter_list or user == USERNAME:
                continue
            voter_list.append(user)
            vote_split = body.split("^^^^")
            if len(vote_split) >= 3:
                vote_text = vote_split[1].lower()
                votes[vote_text] += 1

        return voter_list, votes

    def close_vote(self, voter_list, votes):

        # Add a comment to the issue declaring the vote closed and show the results.
        owner, repo, issue_number = self.get_basic_info()
        close_text = close_comment_vote_text(voter_list, votes)
        self.plugin_inst.github_request(method="post", data={"body": close_text},
             route=f"/repos/{owner}/{repo}/issues/{issue_number}/comments")

        # Close the issue
        self.plugin_inst.github_request(
            method="post", route=f"/repos/{owner}/{repo}/issues/{issue_number}", data={"state": "closed"})

    def close(self, voter_list=None, votes=None):
        if not voter_list or not votes:
            voter_list, votes = self.get_vote_data()
        self.close_vote(voter_list, votes)
        self.status = ProcessStatus.COMPLETED.value
        self.save()
        owner, repo, issue_number = self.get_basic_info()
        logger.info(f"Closing IssueCommentVote {owner}/{repo} - issue # {issue_number}")

    def receive_webhook(self, request):
        action_type, action_target_type, initiator, body = self.plugin_inst.parse_github_webhook(request)
        if action_target_type != "issue_comment" or initiator["user_name"] == USERNAME:
            return
        if body["issue"]["number"] == self.state.get("issue_number") and action_type in ["created", "edited", "deleted"]:
            voter_list, votes = self.get_vote_data()
            if self.state.get("max_votes") and sum(votes.values()) >= self.state.get("max_votes"):
                self.close(voter_list, votes)
