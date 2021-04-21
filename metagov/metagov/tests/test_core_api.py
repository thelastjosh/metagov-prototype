from django.test import TestCase
from metagov.core.models import Community, Plugin
from metagov.plugins.sourcecred.models import SourceCred
from metagov.plugins.example.models import Randomness, StochasticVote
from django.test import Client


class ApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.community_url = "/api/internal/community"

    def test_community(self):
        client = Client()
        community_name = "miriams-community"
        data = {"name": community_name, "readable_name": "miriams new community"}

        # bad request to create community
        response = client.put(f"{self.community_url}/different-name", data=data, content_type="application/json")
        # name and slug dont match
        self.assertContains(response, "Expected name", status_code=400)

        # good request to create community
        url = f"{self.community_url}/{community_name}"
        response = client.put(url, data=data, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Community.objects.all().count(), 1)
        # there should be no plugins
        self.assertEqual(Plugin.objects.all().count(), 0)

        community = Community.objects.all().first()

        # bad request to activate plugin
        data["plugins"] = [{"name": "nonexistent-plugin"}]
        response = client.put(url, data=data, content_type="application/json")
        # name and slug dont match
        self.assertContains(response, "No such plugin registered", status_code=400)

        # bad request to activate plugin
        data["plugins"] = [{"name": "sourcecred", "config": {"wrongkey": "test"}}]
        response = client.put(url, data=data, content_type="application/json")
        self.assertContains(response, "Schema validation error", status_code=400)

        # bad sourcecred request (missing header)
        sourcecred_request_url = "/api/internal/action/sourcecred.user-cred"
        response = client.post(
            sourcecred_request_url,
            data={"parameters": {"username": "miriam"}},
            content_type="application/json",
        )
        self.assertContains(response, "Missing required header 'X-Metagov-Community'", status_code=400)

        # bad sourcecred request (plugin not activated)
        headers = {"HTTP_X_METAGOV_COMMUNITY": community_name}
        sourcecred_request_url = "/api/internal/action/sourcecred.user-cred"
        response = client.post(
            sourcecred_request_url,
            data={"parameters": {"username": "miriam"}},
            content_type="application/json",
            **headers,
        )
        self.assertContains(
            response, "Plugin 'sourcecred' not enabled for community 'miriams-community'", status_code=400
        )

        # good request to activate plugin
        sc_server = "https://metagov.github.io/sourcecred-instance"
        data["plugins"] = [{"name": "sourcecred", "config": {"server_url": sc_server}}]
        response = client.put(url, data=data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        plugins = Plugin.objects.filter(community=community, name="sourcecred")
        self.assertEqual(plugins.count(), 1)
        self.assertEqual(plugins.first().config["server_url"], sc_server)
        sc_proxy_plugins = SourceCred.objects.filter(community=community, name="sourcecred")
        self.assertEqual(sc_proxy_plugins.count(), 1)
        self.assertEqual(sc_proxy_plugins.first().config["server_url"], sc_server)

        # good sourcecred request (plugin is activated)
        sourcecred_request_url = "/api/internal/action/sourcecred.user-cred"
        response = client.post(
            sourcecred_request_url,
            data={"parameters": {"username": "miriam"}},
            content_type="application/json",
            **headers,
        )
        self.assertContains(response, '"value":')

        # activate randomness plugin
        data["plugins"].append({"name": "randomness", "config": {"default_low": 2, "default_high": 200}})
        response = client.put(url, data=data, content_type="application/json")
        self.assertEqual(response.status_code, 200)

        # there are two active plugins: sourcecred and example-plugin
        self.assertEqual(Plugin.objects.filter(community=community).count(), 2)
        # only returns matching proxy models
        self.assertEqual(Randomness.objects.filter(community=community).count(), 1)

        self.assertEqual(Plugin.objects.get(name="randomness").config["default_high"], 200)

        # perform stochastic-vote process

        # start process
        vote_input = {"options": ["one", "two", "three"], "delay": 2}
        response = client.post(
            "/api/internal/process/randomness.delayed-stochastic-vote",
            data=vote_input,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.has_header("location"))
        location = response.get("location")

        # assert created
        self.assertEqual(StochasticVote.objects.all().count(), 1)
        process = StochasticVote.objects.all().first()

        # poll process
        response = client.get(location, content_type="application/json")
        self.assertContains(response, "pending")

        # close process early
        response = client.delete(location, content_type="application/json")
        self.assertContains(response, "completed")
        self.assertContains(response, "winner")

        # deactivate one plugin
        data["plugins"].pop()
        response = client.put(url, data=data, content_type="application/json")
        self.assertEqual(Plugin.objects.filter(community=community).count(), 1)

        # deactivate another plugin
        data["plugins"].pop()
        response = client.put(url, data=data, content_type="application/json")
        self.assertEqual(Plugin.objects.filter(community=community).count(), 0)