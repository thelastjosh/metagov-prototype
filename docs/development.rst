Local Development
=================

Follow these instructions to setup Metagov for local development.
You'll want to do this if...

* You are developing a Metagov Plugin,
* You are developing Metagov Core, or
* You are testing or developing a Driver locally

If you're ready to deploy Metagov to a server, head over to :doc:`Installing Metagov <../installation>` instead.

Run a local Django web server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Clone the Metagov repository (or your fork):

    .. code-block:: shell

        git clone https://github.com/metagov/metagov-prototype.git

2. Navigate to the django project

    .. code-block:: shell

        cd metagov-prototype/metagov

3. Create and activate a Python3 virtual environment:

    .. code-block:: shell

        python3 -m venv env
        source env/bin/activate

4. Install requirements:

    .. code-block:: shell

        pip install --upgrade pip
        pip install -r requirements.txt


5. Set up an ``.env`` file for storing secrets, and generate a new DJANGO_SECRET_KEY:

    .. code-block:: shell

        cp metagov/.env.example metagov/.env
        DJANGO_SECRET_KEY=$(python manage.py shell -c 'from django.core.management import utils; print(utils.get_random_secret_key())')
        echo "DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY" >> metagov/.env

6. (OPTIONAL) Set up a database, and point to it using ``DATABASE_PATH`` in the ``.env`` file. By default, Django will create a sqlite database at ``metagov-prototype/metagov/db.sqlite3``.

7. Run existing migrations:

    .. code-block:: shell

        python manage.py migrate

8.  Start the server:

    .. code-block:: shell

        python manage.py runserver

9. Open http://127.0.0.1:8000/swagger. You should see the interactive API docs.

10. Create a new Community to test with:

    .. code-block:: shell

        curl -i -X PUT 'http://127.0.0.1:8000/api/internal/community/test-community-1' \
            -H 'Content-Type: application/json' \
            --data-raw '{
                "name": "test-community-1",
                "readable_name": "local testing community",
                "plugins": []
            }'

11. In order to perform actions and governance processes, you'll need to activate plugins for this community. See the :doc:`Driver Tutorial <../driver_tutorial>` for some examples.

Tips for Local Development
^^^^^^^^^^^^^^^^^^^^^^^^^^


Testing
-------

Use this command to run all the tests:

    .. code-block:: shell

        python manage.py test

Interactive Django Shell
------------------------

Use the Django shell to interact with the application:

    .. code-block:: shell

        python manage.py shell_plus

        # Useful shell commands:

        # List all communities
        Community.objects.all()

        # List all plugins
        Plugins.objects.all()

        # Get the enabled plugins for a specific community
        community = Community.objects.get(slug='my-community-1234')
        Plugin.objects.filter(community=community)

        # Get the governance processes for a specific community
        GovernanceProcess.objects.filter(plugin__community=community)

        # Get all pending processes
        GovernanceProcess.objects.filter(status='pending')

        # Get all pending DiscoursePoll processes
        DiscoursePoll.objects.filter(status='pending')

        # Manually update a pending processes
        process = DiscoursePoll.objects.filter(status='pending').first()
        process.update()
        process.status
        process.outcome

        # Manually run the plugin tasks that are executed on a schedule by Celery
        from metagov.core.tasks import execute_plugin_tasks
        execute_plugin_tasks()


Making API requests
-------------------

You can use the Swagger documentation to make local requests: http://127.0.0.1:8000/swagger.
For requests that require the ``X-Metagov-Community`` header, make sure you have an existing community with the
necessary plugin enabled.

See the :doc:`Design Overview <../design>` for an overview of the data model and API structure.

Updating this documentation
---------------------------

This documentation is in the `docs <https://github.com/metagov/metagov-prototype/tree/master/docs>`_ directory.
To update it, make changes to the ``.rst`` files.
To generate the documentation locally, run ``make html`` this from the ``docs`` directory, with the metagov virtual environment is activated.

Testing Webhooks
----------------

If you want to test webhook receivers locally, you can use `ngrok <https://ngrok.com/>`_ to create a temporary public URL
for the Metagov Prototype service, and register it with the external platforms while testing.
Make sure to deregister the ngrok URL from the external platform when you're done.


Celery and Scheduled tasks
--------------------------

Some Plugins implement method that will be called by the Celery scheduler.
It's not necessary to set up Celery for local development. If you're developing
a plugin that requires the scheduler to update the process or fetch data, you can test
it out by invoking the task function from the Django shell:

.. code-block:: shell

        python manage.py shell_plus

        from metagov.core.tasks import execute_plugin_tasks
        execute_plugin_tasks()

To set up Celery on an Ubuntu server, follow the instructions at `Installing Metagov <../installation>`_.