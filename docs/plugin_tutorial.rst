Plugin Tutorial
===============

This tutorial will show you have to write a Metagov Plugin. It will demonstrate how to:

1. Create :ref:`Actions` (example: send a tweet)
2. Create a webhook :ref:`Listener` (example: handle events occurring on Open Collective)
3. Create asynchronous :ref:`Governance Processes` (example: perform a vote on Loomio)

This tutorial assumes that you have the Metagov Django service set up for local development.


Creating a Plugin
*****************

Start by creating a new folder with a couple files in it:

.. code-block:: shell

    metagov/plugins/tutorial/
        __init__.py             # required, but can be empty
        models.py               # filename is important

Next, we need to create a `Django proxy <https://docs.djangoproject.com/en/3.1/topics/db/models/#proxy-models>`_ subclass of the ``Plugin`` model.
This new model goes in the ``models.py`` file. Decorate the class with the ``@Registry.plugin`` decorator so that Metagov core picks it up.

.. code-block:: python

    import metagov.core.plugin_decorators as Registry
    from metagov.core.models import Plugin

    @Registry.plugin
    class Tutorial(Plugin):
        name = 'tutorial' # this is a unique slug for the plugin

        class Meta:
            proxy = True # required


Configuration
*************

If the plugin requires any configuration, such as an API key, you can define the configuration
properties as a ``jsonschema`` object. The configuration values will always be Community-specific.

.. code-block:: python

    @Registry.plugin
    class Tutorial(Plugin):
        name = 'tutorial'
        config_schema = {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "foo": {"type": "string", "default": "bar"},
                "webhook_slug": {"type": "string"}
            },
            "required": ["api_key", "foo"]
        }

        class Meta:
            proxy = True

        def initialize(self):
            print(self.config)             # access the current config
            print(self.config["api_key"])  # safely access required properties, it has been validated

Special configuration properties:

* ``webhook_slug``: If you are implementing a webhook listener, you can include this special property. When set, metagov core will expose the webhook endpoint at this slug. This is useful for creating hard-to-guess webhook receiver URL, in cases where the incoming requests can not be validated.


Plugin Lifecycle
****************

Each plugin is a Django model. A new model instance is created each time the plugin is enabled for a given community.

The plugin instance is created when the plugin is enabled for a community, and it is destroyed when the plugin is disabled for that community.
If the community changes the plugin config, the plugin instance gets destroyed and recreated.

Initialize
^^^^^^^^^^

Override the ``initialize`` function to set up the plugin. It is called exactly once, when the plugin is created.


Persisting data
^^^^^^^^^^^^^^^

There may be times when you want to persist community-related data, so that it can be accessed by all actions, processes, and listeners.
This is possible by using the ``state`` attribute on the Plugin.
The data stored in ``state`` must be serializable using `jsonpickle <https://jsonpickle.github.io/>`_.

.. code-block:: python

    @Registry.plugin
    class Tutorial(Plugin):
        #..elided..

        def initialize(self):
            # ✅ set initial state
            self.state.set("foo", "bar")

            # 🛑 this won't be persisted
            self.foo = "bar"

        def my_action(self):
            value = self.state.get("foo")     # access state
            self.state.set("obj", {"x": 2})   # update state

.. note:: If the plugin config is changed, the plugin instance gets destroyed and recreated. At that point, all ``state`` is lost.

Enabling the Plugin for a Community
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To create a new community with your plugin activated, make a PUT request to the ``community`` endpoint:

.. code-block:: shell

    curl -X PUT 'http://127.0.0.1:8000/api/internal/community/my-community-1234' \
        -H 'Content-Type: application/json' \
        --data-raw '{
            "name": "my-community-1234",
            "readable_name": "",
            "plugins": [
                {
                    "name": "tutorial",
                    "config": {
                        "api_key": "ABC123",
                        "foo": "baz"
                    }
                }
            ]
        }'


See the Design Overview for more information about the data model.

Disabling the Plugin for a Community
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Disable the plugin by removing it from the community ``plugins`` list. When the plugin is disabled,
the Plugin model instance is deleted, and all data in ``state`` is lost.

.. code-block:: shell

    curl -X PUT 'http://127.0.0.1:8000/api/internal/community/my-community-1234' \
        -H 'Content-Type: application/json' \
        --data-raw '{
            "name": "my-community-1234",
            "readable_name": "",
            "plugins": []
        }'


Actions
*******

If you want to expose a way for the governance driver to **perform an action** or **get data**,
then you can implement an action. An action is just a function on your Plugin class that is registered
with metagov core, and exposed as an API endpoint at ``/api/internal/action/<plugin>.<slug>``.

All you need to do is decorate your function with the ``@Registry.action`` decorator:

..
   _TODO define error type and structure

.. code-block:: python

    @Registry.plugin
    class Tutorial(Plugin):
        #..elided..

        @Registry.action(
            slug='times-two',
            description='description for OpenAPI docs',
            input_schema=my_input_schema,     # optional jsonschema for parameters
            output_schema=my_output_schema    # optional jsonschema for return value
        )
        def times_two(self, parameters):
            num = parameters["value"]     # parameters have been validated against `my_input_schema`
            print(self.config["foo"])     # access the plugin configuration or plugin state, if needed
            return {"result": num * 2 }   # output will be validated against `my_output_schema`


Now you should be able to invoke the action through the Metagov API:

.. code-block:: shell

    curl -X POST 'http://127.0.0.1:8000/api/internal/action/tutorial.times-two' \
        -H 'Content-Type: application/json' \
        -H 'X-Metagov-Community: my-community-1234' \
        --data-raw '{
            "parameters": { "value": 5 }
        }'


Listener
********

If you want to listen to events occurring on another platform, and forward them to the governance driver so that
it can react to them, then you want to implement a **listener** in your plugin.
Listeners can either use **Webhooks** (data is "pushed" from the external platform to Metagov) or **Tasks** (Metagov pulls data from the external platform).

Webhooks
^^^^^^^^
If the external platform supports webhooks, use the ``webhook_receiver`` decorator to register a handler for processing incoming
webhooks from the platform. Use the ``send_event_to_driver`` function to send the event to the Driver. Example:

.. code-block:: python

    @Registry.plugin
    class Tutorial(Plugin):
        #..elided..

        @Registry.webhook_receiver()
        def my_webhook_receiver(self, request):
            body = json.loads(request.body)   # Django HttpRequest object
            print(body)
            data = body["data"]
            initiator = { "user_id": body["account"], "provider": "identity-provider-key" }
            # send the event to the driver
            self.send_event_to_driver(event_type="post_created", data=data, initiator=initiator)


Tasks
^^^^^

If the external platform does not support webhooks, you can use the ``event_producer_task`` decorator to register a task function to poll the external service.
Metagov core will call the registered task function on a schedule. The schedule is defined in ``settings.py``
under ``plugin-tasks-beat``. The same schedule is used for all plugins, for now.

.. code-block:: python

    @Registry.plugin
    class Tutorial(Plugin):
        #..elided..

        @Registry.event_producer_task()
        def my_task_function(self):
            # make a request for recent events
            # send event to the driver
            self.send_event_to_driver(...)


See :doc:`Reference Documentation <../autodocs/core>` for the full specification.

Webhook Receiver URLs
^^^^^^^^^^^^^^^^^^^^^

If your plugin defines a ``webhook_receiver`` function,
Metagov core will expose a dedicated endpoint for each plugin instance
to receive webhook requests.

For the plugin and community we created in this tutorial, the webhook receiver endpoint is either at: ``http://127.0.0.1:8000/api/hooks/community-slug/tutorial`` or ``http://127.0.0.1:8000/api/hooks/community-slug/tutorial/<webhook_slug>``, depending on whether the ``webhook_slug`` config option was set for the community. The community slug is a unique string of letters and numbers generated and returned to you by Metagov when you create your community.

Incoming POST requests to this endpoint will be routed to the method that is decorated with the ``webhook_receiver`` decorator.

You can test out your webhook receiver by using `ngrok <https://ngrok.com/>`_ to create a temporary public URL for your local development server.
Then, go to the external platform (Discourse, Open Collective, etc) and register your temporary URL. It will look something like:
``https://abc123.ngrok.io/api/hooks/my-community-1234/tutorial``. Now, when you perform actions on the external platform, you should see events logged locally from your webhook receiver function.

.. note:: Get a list of all the webhook receiver endpoints for your community:

    .. code-block:: shell

        curl 'http://127.0.0.1:8000/api/internal/community/my-community-1234/hooks'


Validating webhook requests
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Anyone on the internet can post requests to the metagov webhook receiver endpoints, so it's important to always verify the incoming requests to the extent possible. Some suggestions:

1. Ideally, the request can be verified using an event signature. This is not supported by all platforms. See the Discourse plugin for an example.
2. Use a hard-to-guess URL. The community slug should already be hard-to-guess, but we can make it even more difficult by setting the ``webhook_slug`` config property to a random string. The URL ends up looking like ``/api/hooks/<community_slug>/<plugin_name>/<webhook_slug>`` which is pretty hard to guess, so you can be reasonably sure that it's coming from the right place.
3. Don't rely on data in the webhook body. Always get data from the platform API instead of relying on what is in the webhook body. That way, even if the request is spoofed, we can find out from the platform API. See OpenCollective plugin for an example.


Governance Processes
********************

If you want to expose a way for the governance driver to perform an asynchronous governance process
(such as a vote, election, or budgeting process) then you can implement a Governance Process. Governance
processes are exposed as API endpoints at ``/api/internal/process/<plugin>.<slug>``.

Create a proxy subclass of the ``GovernanceProcess`` Django model for our new governance process, ``MyGovProcess``.
This model should be declared after the ``Tutorial`` model. Decorate it with the ``@Registry.governance_process``
decorator so that Metagov core picks it up. In this example, the process will be exposed as an endpoint
at ``/process/tutorial.my-gov-process``.

This snippet shows all possible functions you can implement on your proxy model:

.. code-block:: python

    @Registry.governance_process
    class MyGovProcess(GovernanceProcess):
        name = 'my-gov-process'
        plugin_name = 'tutorial'
        input_schema = {} # optional jsonschema for validation

        class Meta:
            proxy = True

        def start(self, parameters):
            # Override this function (REQUIRED).
            # Kick off the asynchronous governance process and return immediately.
            pass

        def close(self):
            # Override this function (OPTIONAL).
            # Close the governance process and save the outcome.
            pass

        def update(self):
            # Override this function (OPTIONAL).
            # Update status and/or outcome, if applicable. This function is called repeatedly on a schedule.
            pass

        def receive_webhook(self, request):
            # Override this function (OPTIONAL).
            # Receive incoming webhook request for plugin instance.
            # Update status and/or outcome, if applicable.
            pass


Starting a governance process
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Implement the ``start`` method to kick off a new asynchronous governance process.
Set the status to ``ProcessStatus.PENDING`` (or ``ProcessStatus.COMPLETED`` if unable to start the process).
This method will be invoked through ``POST /api/internal/process/tutorial.my-gov-process``.

Closing a governance process
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

There are multiple ways that a governance process can be "closed." A plugin may support one or several of them.
A process is considered closed when the status is set to ``ProcessStatus.COMPLETED``.
Using the voting platform Loomio as an example, a vote can be closed in 3 ways:

1) Loomio automatically closes the vote at a specified time ("closing_at").
2) A Loomio user clicks "close proposal early" in the Loomio interface.
3) The Driver closes the vote by making an API request to ``DELETE /api/internal/process/loomio.poll/<id>``. It may do this after a certain amount of time, or when a certain threshold of votes is reached, or for some other reason.

To support (1) and (2), Metagov needs to be made aware that the platform has closed the vote. This can happen through a "push" or "pull" approach, depending on the capabilities of the platform (see below).

To support (3), the governance process needs to implement the ``close`` function. In order to support the driver in making a threshold-decision about when to close, use the "push" or "pull" approach to update the process outcome as votes are cast.


..
    Add fourth approach: Metagov-as-time-keeper.

**PUSH approach: Use "receive_webhook" to get notified when the state of the process changes.**

Use this approach if you're implementing a process that is performed on an external
platform that is capable of emitting a webhook when the process ends (and/or when the process changes, such as a vote is cast).
Implement the ``receive_webhook`` listener. Use it to update status and outcome, if applicable.
See the Loomio plugin for an example.

**PULL approach: Use "update" to poll for changes in the process.**

Implement ``update`` to check the status of the async process, possibly by making
a request to an external platform. Update status and outcome, if applicable.
Metagov core calls the ``update`` function every minute from a scheduled task.
See the Discourse plugin for an example.

.. seealso:: See the :doc:`Reference Documentation <../autodocs/core>` for more information about the ``GovernanceProcess`` models.

.. seealso:: Once you've implemented a governance process, you can invoke it through the Metagov API. See the `Example Driver Repo <https://github.com/metagov/example-driver>`_ for an example of kicking off a governance process and waiting for the result at a ``callback_url``.


Re-opening a governance process
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Not currently supported. Once a process moves into ``ProcessStatus.COMPLETED`` state, it cannot be re-opened.
